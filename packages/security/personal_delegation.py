from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select

from packages.database.account_connector_models import AccountConnector
from packages.database.models import TenantMember
from packages.database.principal_models import Principal
from packages.database.scope_models import DelegatedCapabilityAudit, PersonalWorkspaceDelegation


class DelegationError(PermissionError):
    pass


async def _principal(db, user_id: str) -> Principal:
    row = await db.scalar(select(Principal).where(Principal.kind == "human", Principal.user_id == user_id))
    if row is None:
        row = Principal(kind="human", user_id=user_id, status="active")
        db.add(row)
        await db.flush()
    return row


async def _membership(db, user_id: str, tenant_id: str) -> TenantMember:
    membership = await db.scalar(
        select(TenantMember).where(
            TenantMember.user_id == user_id,
            TenantMember.tenant_id == tenant_id,
        )
    )
    if membership is None:
        raise DelegationError("You are not a member of that workspace")
    return membership


async def grant_delegation(
    db,
    *,
    user_id: str,
    tenant_id: str,
    capability_id: str,
    connector_reference: str | None = None,
    scope: dict[str, Any] | None = None,
    grant_type: str = "persistent",
    action_id: str | None = None,
    expires_at: datetime | None = None,
) -> PersonalWorkspaceDelegation:
    await _membership(db, user_id, tenant_id)
    kind = str(grant_type or "persistent").strip().lower()
    if kind not in {"persistent", "one_time"}:
        raise ValueError("grant_type must be persistent or one_time")
    capability = str(capability_id or "").strip()
    if not capability or len(capability) > 160:
        raise ValueError("capability_id is required")
    connector = None
    if connector_reference:
        connector = await db.scalar(
            select(AccountConnector).where(
                AccountConnector.id == connector_reference,
                AccountConnector.user_id == user_id,
                AccountConnector.enabled.is_(True),
            )
        )
        if connector is None:
            raise DelegationError("Personal connector not found")
    principal = await _principal(db, user_id)
    if kind == "one_time" and not action_id:
        # A one-time grant may be unbound briefly while an Action is proposed, but
        # it must still expire soon through the caller-supplied expiry. Persistent
        # grants are the only grants allowed without action/expiry bounds.
        if expires_at is None:
            raise ValueError("one_time delegation requires action_id or expires_at")
    row = PersonalWorkspaceDelegation(
        principal_id=principal.id,
        user_id=user_id,
        tenant_id=tenant_id,
        capability_id=capability,
        connector_reference=connector.id if connector else None,
        scope_json=json.dumps(scope or {}, ensure_ascii=False, sort_keys=True, default=str),
        grant_type=kind,
        action_id=action_id,
        status="active",
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    db.add(
        DelegatedCapabilityAudit(
            delegation_id=row.id,
            principal_id=principal.id,
            tenant_id=tenant_id,
            capability_id=capability,
            action_id=action_id,
            outcome="granted",
            evidence_json=json.dumps({"grant_type": kind, "connector_reference": connector.id if connector else None}),
        )
    )
    await db.flush()
    return row


async def revoke_delegation(db, *, user_id: str, delegation_id: str) -> PersonalWorkspaceDelegation:
    row = await db.scalar(
        select(PersonalWorkspaceDelegation).where(
            PersonalWorkspaceDelegation.id == delegation_id,
            PersonalWorkspaceDelegation.user_id == user_id,
        )
    )
    if row is None:
        raise LookupError("Delegation not found")
    if row.status == "active":
        row.status = "revoked"
        row.revoked_at = datetime.utcnow()
        db.add(
            DelegatedCapabilityAudit(
                delegation_id=row.id,
                principal_id=row.principal_id,
                tenant_id=row.tenant_id,
                capability_id=row.capability_id,
                action_id=row.action_id,
                outcome="revoked",
                evidence_json="{}",
            )
        )
    await db.flush()
    return row


async def active_delegation(
    db,
    *,
    user_id: str,
    tenant_id: str,
    capability_id: str,
    action_id: str | None = None,
    connector_provider: str | None = None,
) -> tuple[PersonalWorkspaceDelegation, AccountConnector | None] | None:
    # Membership is checked on every use, not only when the grant was created.
    try:
        await _membership(db, user_id, tenant_id)
    except DelegationError:
        return None
    now = datetime.utcnow()
    rows = list(
        (
            await db.scalars(
                select(PersonalWorkspaceDelegation)
                .where(
                    PersonalWorkspaceDelegation.user_id == user_id,
                    PersonalWorkspaceDelegation.tenant_id == tenant_id,
                    PersonalWorkspaceDelegation.capability_id == capability_id,
                    PersonalWorkspaceDelegation.status == "active",
                )
                .order_by(PersonalWorkspaceDelegation.created_at.desc())
            )
        ).all()
    )
    for row in rows:
        if row.expires_at is not None and row.expires_at <= now:
            row.status = "expired"
            continue
        if row.grant_type == "one_time" and row.action_id and row.action_id != action_id:
            continue
        connector = None
        if row.connector_reference:
            connector = await db.scalar(
                select(AccountConnector).where(
                    AccountConnector.id == row.connector_reference,
                    AccountConnector.user_id == user_id,
                    AccountConnector.enabled.is_(True),
                    AccountConnector.status == "connected",
                )
            )
            if connector is None:
                continue
            if connector_provider and connector.provider != connector_provider:
                continue
        return row, connector
    return None


async def authorize_delegated_use(
    db,
    *,
    user_id: str,
    tenant_id: str,
    capability_id: str,
    action_id: str | None = None,
    connector_provider: str | None = None,
) -> tuple[PersonalWorkspaceDelegation, AccountConnector | None] | None:
    resolved = await active_delegation(
        db,
        user_id=user_id,
        tenant_id=tenant_id,
        capability_id=capability_id,
        action_id=action_id,
        connector_provider=connector_provider,
    )
    if resolved is None:
        return None
    row, connector = resolved
    row.last_used_at = datetime.utcnow()
    if row.grant_type == "one_time":
        # Consume before the external side effect so retries cannot double-use a
        # one-time personal authority grant after a transport ambiguity.
        row.status = "consumed"
    db.add(
        DelegatedCapabilityAudit(
            delegation_id=row.id,
            principal_id=row.principal_id,
            tenant_id=row.tenant_id,
            capability_id=row.capability_id,
            action_id=action_id,
            outcome="authorized_use",
            evidence_json=json.dumps(
                {"grant_type": row.grant_type, "connector_reference": row.connector_reference},
                sort_keys=True,
            ),
        )
    )
    await db.flush()
    return row, connector


async def audit_delegated_result(
    db,
    *,
    delegation: PersonalWorkspaceDelegation,
    action_id: str | None,
    outcome: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    db.add(
        DelegatedCapabilityAudit(
            delegation_id=delegation.id,
            principal_id=delegation.principal_id,
            tenant_id=delegation.tenant_id,
            capability_id=delegation.capability_id,
            action_id=action_id,
            outcome=str(outcome)[:40],
            evidence_json=json.dumps(evidence or {}, ensure_ascii=False, sort_keys=True, default=str)[:16000],
        )
    )
    await db.flush()


async def list_delegations(db, *, user_id: str) -> list[PersonalWorkspaceDelegation]:
    return list(
        (
            await db.scalars(
                select(PersonalWorkspaceDelegation)
                .where(PersonalWorkspaceDelegation.user_id == user_id)
                .order_by(PersonalWorkspaceDelegation.created_at.desc())
            )
        ).all()
    )
