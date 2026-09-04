from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.kernel_models import KernelApproval
from packages.security.execution_context import ExecutionContext


class ApprovalError(RuntimeError):
    pass


def arguments_hash(capability_id: str, arguments: dict[str, Any]) -> str:
    raw = json.dumps(
        {"capability_id": capability_id, "arguments": arguments},
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


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
    digest = arguments_hash(capability_id, arguments)
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
    if existing is not None:
        return existing

    row = KernelApproval(
        scope_kind=context.scope_kind.value,
        workspace_id=context.workspace_id,
        owner_user_id=context.user_id if context.is_personal else None,
        requested_by_principal_id=context.principal_id,
        requested_by_user_id=context.user_id,
        capability_id=capability_id,
        arguments_hash=digest,
        arguments_json=json.dumps(arguments, separators=(",", ":"), sort_keys=True, default=str),
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
    # This row lock is intentionally held through provider execution and the final
    # consume_approval() call in the Kernel transaction. A second resume therefore
    # cannot validate the same approved action concurrently; once the first commits,
    # the second observes "consumed" and fails closed before any side effect.
    row = await approval_for_context(
        db, context=context, approval_id=approval_id, lock=True
    )
    if row.status != "approved":
        raise ApprovalError(f"Approval is not executable: {row.status}")
    if row.requested_by_principal_id and row.requested_by_principal_id != context.principal_id:
        raise ApprovalError("Approval belongs to a different initiating principal")
    if row.capability_id != capability_id:
        raise ApprovalError("Approval is bound to a different capability")
    if row.arguments_hash != arguments_hash(capability_id, arguments):
        raise ApprovalError("Approval arguments do not match the authorized invocation")
    return row


async def consume_approval(
    db: AsyncSession,
    *,
    approval: KernelApproval | None,
    run_id: str,
) -> None:
    if approval is None:
        return
    if approval.status != "approved":
        raise ApprovalError("Only an approved invocation can be consumed")
    approval.status = "consumed"
    approval.consumed_run_id = run_id
    approval.consumed_at = datetime.utcnow()
    await db.flush()


def approval_json(row: KernelApproval, *, include_arguments: bool = False) -> dict[str, Any]:
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
        "status": row.status,
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
