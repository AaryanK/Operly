from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.kernel_models import KernelApproval
from packages.security.execution_context import ExecutionContext


class ApprovalError(RuntimeError):
    pass


APPROVAL_TTL = timedelta(minutes=15)


def canonical_arguments(capability_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return the exact server-side representation an approval authorizes.

    Most capabilities already execute their validated arguments verbatim. Gmail send
    normalizes address/header whitespace before constructing the RFC message, so bind
    approval to that same canonical representation. This prevents the review payload
    from differing from the message that is actually sent while still treating
    semantically irrelevant surrounding whitespace as the same invocation.
    """

    if capability_id != "google.gmail.send_email":
        return dict(arguments)

    normalized: dict[str, Any] = {
        "to": [str(item).strip() for item in arguments.get("to", []) if str(item).strip()],
        "cc": [str(item).strip() for item in arguments.get("cc", []) if str(item).strip()],
        "bcc": [str(item).strip() for item in arguments.get("bcc", []) if str(item).strip()],
        "subject": str(arguments.get("subject") or "").strip()[:998],
        "text_body": str(arguments.get("text_body") or "")[:50000],
    }
    connector_id = str(arguments.get("connector_id") or "").strip()
    if connector_id:
        normalized["connector_id"] = connector_id
    reply_to = str(arguments.get("reply_to") or "").strip()
    if reply_to:
        normalized["reply_to"] = reply_to
    return normalized


def arguments_hash(capability_id: str, arguments: dict[str, Any]) -> str:
    raw = json.dumps(
        {
            "capability_id": capability_id,
            "arguments": canonical_arguments(capability_id, arguments),
        },
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def approval_expires_at(row: KernelApproval) -> datetime:
    # Persisted approvals always have created_at. The fallback exists only for older
    # contract-test doubles that predate expiry metadata; it cannot be populated by a
    # client/model and therefore does not weaken the database-backed production path.
    created_at = getattr(row, "created_at", None)
    if not isinstance(created_at, datetime):
        created_at = datetime.utcnow()
    return created_at + APPROVAL_TTL


def _ensure_fresh(row: KernelApproval) -> None:
    if datetime.utcnow() >= approval_expires_at(row):
        raise ApprovalError("Approval has expired")


def _scope_filters(context: ExecutionContext):
    if context.is_personal:
        if not context.user_id:
            raise ApprovalError("Personal approval requires an owner")
        return (
            KernelApproval.scope_kind == "personal",
            KernelApproval.owner_user_id == context.user_id,
            KernelApproval.workspace_id.is_(None),
        )
    if not context.workspace_id:
        raise ApprovalError("Workspace approval requires workspace authority")
    return (
        KernelApproval.scope_kind == "workspace",
        KernelApproval.workspace_id == context.workspace_id,
    )


async def create_pending_approval(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    capability_id: str,
    arguments: dict[str, Any],
    request_id: str | None,
    conversation_id: str | None,
    source_run_id: str,
) -> KernelApproval:
    canonical = canonical_arguments(capability_id, arguments)
    digest = arguments_hash(capability_id, canonical)
    filters = [
        *_scope_filters(context),
        KernelApproval.requested_by_principal_id == context.principal_id,
        KernelApproval.capability_id == capability_id,
        KernelApproval.arguments_hash == digest,
        KernelApproval.status == "pending",
    ]
    clean_request_id = str(request_id or "").strip() or None
    if clean_request_id:
        filters.append(KernelApproval.request_id == clean_request_id)
    existing = await db.scalar(
        select(KernelApproval)
        .where(*filters)
        .order_by(KernelApproval.created_at.desc())
    )
    if existing is not None and datetime.utcnow() < approval_expires_at(existing):
        return existing

    row = KernelApproval(
        scope_kind=context.scope_kind.value,
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id if context.is_personal else None,
        requested_by_principal_id=context.principal_id,
        requested_by_user_id=context.user_id,
        capability_id=capability_id,
        arguments_hash=digest,
        arguments_json=json.dumps(canonical, separators=(",", ":"), sort_keys=True, default=str),
        request_id=clean_request_id,
        conversation_id=conversation_id,
        source_run_id=source_run_id,
        status="pending",
    )
    db.add(row)
    await db.flush()
    return row


async def approval_for_context(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    approval_id: str,
    lock: bool = False,
) -> KernelApproval:
    statement = select(KernelApproval).where(
        KernelApproval.id == approval_id,
        *_scope_filters(context),
    )
    if lock:
        statement = statement.with_for_update()
    row = await db.scalar(statement)
    if row is None:
        raise ApprovalError("Approval is unavailable in this scope")
    return row


def _validate_binding(
    row: KernelApproval,
    *,
    context: ExecutionContext,
    capability_id: str,
    arguments: dict[str, Any],
) -> None:
    _ensure_fresh(row)
    if row.requested_by_principal_id and row.requested_by_principal_id != context.principal_id:
        raise ApprovalError("Approval belongs to a different initiating principal")
    if row.capability_id != capability_id:
        raise ApprovalError("Approval is bound to a different capability")
    if row.arguments_hash != arguments_hash(capability_id, arguments):
        raise ApprovalError("Approval arguments do not match the authorized invocation")


async def decide_approval(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    approval_id: str,
    approved: bool,
    decided_by_user_id: str,
) -> KernelApproval:
    # Serialize competing human decisions. Only one transaction may transition a
    # pending approval, which also gives the execution path a stable state to lock.
    row = await approval_for_context(
        db, context=context, approval_id=approval_id, lock=True
    )
    _ensure_fresh(row)
    if row.status != "pending":
        raise ApprovalError(f"Approval is already {row.status}")
    row.status = "approved" if approved else "denied"
    row.decided_by_user_id = decided_by_user_id
    row.decided_at = datetime.utcnow()
    await db.flush()
    return row


async def validate_approved_invocation(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    approval_id: str,
    capability_id: str,
    arguments: dict[str, Any],
) -> KernelApproval:
    # This authorization-stage check intentionally does not consume the approval.
    # Mutating requests atomically and durably transition approved -> executing in
    # the idempotency reservation immediately before provider execution.
    row = await approval_for_context(
        db, context=context, approval_id=approval_id, lock=True
    )
    if row.status != "approved":
        raise ApprovalError(f"Approval is not executable: {row.status}")
    _validate_binding(
        row,
        context=context,
        capability_id=capability_id,
        arguments=arguments,
    )
    return row


async def claim_approved_invocation(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    approval_id: str,
    request_id: str,
    capability_id: str,
    arguments: dict[str, Any],
) -> KernelApproval:
    """Atomically claim one exact approved mutation before its provider side effect.

    The approval is tied to the stable request ID that originally requested human
    authorization. A concurrent resume using the same approval but a different
    request ID cannot pass this transition. The caller must commit the transition
    before provider execution so a process crash cannot resurrect the approval.
    """

    row = await approval_for_context(
        db, context=context, approval_id=approval_id, lock=True
    )
    if row.status != "approved":
        raise ApprovalError(f"Approval is not executable: {row.status}")
    _validate_binding(
        row,
        context=context,
        capability_id=capability_id,
        arguments=arguments,
    )
    original_request_id = str(row.request_id or "").strip()
    current_request_id = str(request_id or "").strip()
    if not original_request_id:
        raise ApprovalError("Approval is missing its original stable request_id")
    if not current_request_id or current_request_id != original_request_id:
        raise ApprovalError("Approval is bound to a different request_id")
    row.status = "executing"
    await db.flush()
    return row


async def consume_approval(
    db: AsyncSession,
    *,
    approval: KernelApproval | None,
    run_id: str,
) -> None:
    if approval is None:
        return
    if approval.status not in {"approved", "executing"}:
        raise ApprovalError("Only an approved or executing invocation can be consumed")
    approval.status = "consumed"
    approval.consumed_run_id = run_id
    approval.consumed_at = datetime.utcnow()
    await db.flush()


def approval_json(row: KernelApproval, *, include_arguments: bool = False) -> dict[str, Any]:
    expires_at = approval_expires_at(row)
    effective_status = row.status
    if effective_status in {"pending", "approved"} and datetime.utcnow() >= expires_at:
        effective_status = "expired"
    payload = {
        "id": row.id,
        "scope_kind": row.scope_kind,
        "workspace_id": row.workspace_id,
        "owner_user_id": row.owner_user_id,
        "requested_by_principal_id": row.requested_by_principal_id,
        "requested_by_user_id": row.requested_by_user_id,
        "capability_id": row.capability_id,
        "request_id": row.request_id,
        "conversation_id": row.conversation_id,
        "source_run_id": row.source_run_id,
        "status": effective_status,
        "arguments_hash": row.arguments_hash,
        "expires_at": expires_at.isoformat(),
        "decided_by_user_id": row.decided_by_user_id,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "consumed_run_id": row.consumed_run_id,
        "consumed_at": row.consumed_at.isoformat() if row.consumed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if include_arguments:
        try:
            payload["arguments"] = json.loads(row.arguments_json or "{}")
        except json.JSONDecodeError:
            payload["arguments"] = {}
    return payload
