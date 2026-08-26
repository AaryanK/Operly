from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.account_connector_models import AccountConnector
from packages.database.channel_models import ExternalIdentity
from packages.database.models import AppUser, AuthIdentity, Tenant, TenantMember
from packages.database.principal_models import ExternalPrincipalBinding, Principal


@dataclass(frozen=True, slots=True)
class HumanIdentitySnapshot:
    user_id: str
    display_name: str
    email: str
    auth_identities: tuple[dict, ...]
    external_identities: tuple[dict, ...]
    provider_accounts: tuple[dict, ...]
    principals: tuple[dict, ...]
    workspaces: tuple[dict, ...]

    def as_dict(self) -> dict:
        return {
            "human": {
                "user_id": self.user_id,
                "display_name": self.display_name,
                "email": self.email,
            },
            "auth_identities": list(self.auth_identities),
            "external_identities": list(self.external_identities),
            "provider_accounts": list(self.provider_accounts),
            "principals": list(self.principals),
            "workspaces": list(self.workspaces),
        }


class HumanIdentityService:
    """Project every verified/provider identity onto one Operly human.

    The AppUser id is the canonical human id. Provider identities, authentication
    identities, account connectors and runtime principals remain separate records so
    revocation and authorization never leak between providers, but callers resolve
    them through this one graph instead of treating them as different people.
    """

    @staticmethod
    def _json(value: str | None) -> dict:
        try:
            parsed = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    async def snapshot(cls, db: AsyncSession, *, user_id: str) -> HumanIdentitySnapshot:
        user = await db.get(AppUser, user_id)
        if user is None or not user.active:
            raise LookupError("Operly user is unavailable")

        auth_rows = list(
            (
                await db.scalars(
                    select(AuthIdentity)
                    .where(AuthIdentity.user_id == user_id)
                    .order_by(AuthIdentity.provider, AuthIdentity.created_at)
                )
            ).all()
        )
        external_rows = list(
            (
                await db.scalars(
                    select(ExternalIdentity)
                    .where(ExternalIdentity.user_id == user_id)
                    .order_by(ExternalIdentity.provider, ExternalIdentity.created_at)
                )
            ).all()
        )
        connector_rows = list(
            (
                await db.scalars(
                    select(AccountConnector)
                    .where(AccountConnector.user_id == user_id)
                    .order_by(AccountConnector.provider, AccountConnector.created_at)
                )
            ).all()
        )
        principal_rows = list(
            (
                await db.scalars(
                    select(Principal)
                    .where(Principal.user_id == user_id)
                    .order_by(Principal.created_at)
                )
            ).all()
        )
        principal_ids = [row.id for row in principal_rows]
        binding_rows = (
            list(
                (
                    await db.scalars(
                        select(ExternalPrincipalBinding)
                        .where(ExternalPrincipalBinding.principal_id.in_(principal_ids))
                        .order_by(
                            ExternalPrincipalBinding.provider,
                            ExternalPrincipalBinding.created_at,
                        )
                    )
                ).all()
            )
            if principal_ids
            else []
        )
        bindings_by_principal: dict[str, list[dict]] = {}
        for row in binding_rows:
            bindings_by_principal.setdefault(row.principal_id, []).append(
                {
                    "provider": row.provider,
                    "provider_subject": row.provider_subject,
                    "display_name": row.display_name,
                    "verified": bool(row.verified),
                }
            )

        memberships = list(
            (
                await db.execute(
                    select(TenantMember, Tenant)
                    .join(Tenant, Tenant.id == TenantMember.tenant_id)
                    .where(TenantMember.user_id == user_id)
                    .order_by(Tenant.name)
                )
            ).all()
        )

        return HumanIdentitySnapshot(
            user_id=user.id,
            display_name=user.display_name,
            email=user.email,
            auth_identities=tuple(
                {
                    "provider": row.provider,
                    "provider_subject": row.provider_subject,
                    "provider_email": row.provider_email,
                }
                for row in auth_rows
            ),
            external_identities=tuple(
                {
                    "id": row.id,
                    "provider": row.provider,
                    "provider_subject": row.provider_subject,
                    "display_name": row.display_name,
                    "verified": row.verified_at is not None,
                    "metadata": cls._json(row.metadata_json),
                }
                for row in external_rows
            ),
            provider_accounts=tuple(
                {
                    "id": row.id,
                    "provider": row.provider,
                    "connector_type": row.connector_type,
                    "provider_account_id": row.provider_account_id,
                    "display_name": row.display_name,
                    "status": row.status,
                    "enabled": bool(row.enabled),
                    "health_status": row.health_status,
                }
                for row in connector_rows
            ),
            principals=tuple(
                {
                    "id": row.id,
                    "kind": row.kind,
                    "status": row.status,
                    "display_name": row.display_name,
                    "external_bindings": bindings_by_principal.get(row.id, []),
                }
                for row in principal_rows
            ),
            workspaces=tuple(
                {
                    "workspace_id": workspace.id,
                    "workspace_name": workspace.name,
                    "role": membership.role,
                }
                for membership, workspace in memberships
            ),
        )
