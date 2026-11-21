from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.database.kernel_models import KernelApproval, KernelEventRecord, KernelRun, KernelRunStep
from packages.kernel import RuntimeExecutionError, build_kernel_runtime
from packages.kernel.approvals import ApprovalError, approval_for_context, approval_json, decide_approval
from packages.kernel.contracts import RuntimeRequest
from packages.database.models import Tenant, TenantMember
from packages.personal_modules.runtime import build_personal_runtime
from packages.kernel.ingress import TrustedIngress, resolve_ingress_context
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


router = APIRouter(prefix="/api/kernel", tags=["kernel-personal"])
_runtime = build_kernel_runtime()


class KernelExecuteInput(BaseModel):
    goal: str = Field(default="", max_length=4000)
    capability_id: str | None = Field(default=None, max_length=160)
    arguments: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str | None = Field(default=None, max_length=160)
    request_id: str | None = Field(default=None, max_length=160)
    approval_id: str | None = Field(default=None, max_length=80)


class ApprovalDecisionInput(BaseModel):
    approved: bool


async def _personal_context(
    db: AsyncSession,
    account: AccountAuthContext,
    conversation_id: str | None = None,
) -> ExecutionContext:
    return await resolve_ingress_context(
        db,
        TrustedIngress(
            scope_kind=ScopeKind.PERSONAL,
            user_id=account.user.id,
            workspace_id=None,
            channel="web",
            surface=SurfaceKind.PERSONAL_PRIVATE,
            conversation_id=conversation_id,
            metadata={"ingress": "operly_personal_kernel"},
        ),
    )


def _runtime_request(payload: KernelExecuteInput) -> RuntimeRequest:
    return RuntimeRequest(
        goal=payload.goal,
        capability_id=payload.capability_id,
        arguments=payload.arguments,
        conversation_id=payload.conversation_id,
        request_id=payload.request_id,
        approval_id=payload.approval_id,
    )


async def _execute(db: AsyncSession, context: ExecutionContext, payload: KernelExecuteInput):
    try:
        response = await _runtime.execute(db, context=context, request=_runtime_request(payload))
    except RuntimeExecutionError as error:
        detail = {"code": error.code, "message": str(error), "run_id": error.run_id}
        if error.approval_id:
            detail["approval_id"] = error.approval_id
        raise HTTPException(status_code=error.status_code, detail=detail) from error
    return response.as_dict()


def _json(value: str) -> Any:
    try:
        return json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}


async def _run_payload(db: AsyncSession, run: KernelRun) -> dict[str, Any]:
    steps = (
        await db.scalars(
            select(KernelRunStep)
            .where(KernelRunStep.run_id == run.id)
            .order_by(KernelRunStep.step_number, KernelRunStep.created_at)
        )
    ).all()
    return {
        "id": run.id,
        "scope_kind": run.scope_kind,
        "owner_user_id": run.owner_user_id,
        "principal_id": run.principal_id,
        "channel": run.channel,
        "surface": run.surface,
        "conversation_id": run.conversation_id,
        "goal": run.goal,
        "capability_id": run.capability_id,
        "status": run.status,
        "result": _json(run.result_json),
        "error": run.error,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "steps": [
            {
                "step": step.step_number,
                "name": step.step_name,
                "status": step.status,
                "payload": _json(step.payload_json),
                "created_at": step.created_at.isoformat(),
            }
            for step in steps
        ],
    }


@router.get("/personal/capabilities")
async def personal_capabilities(
    query: str | None = Query(default=None, max_length=200),
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _personal_context(db, account)
    specs = await _runtime.available_capabilities(db, context=context, query=query, limit=50)
    return {
        "scope_kind": "personal",
        "user_id": account.user.id,
        "capabilities": [spec.public_dict() for spec in specs if "personal" in spec.scopes],
    }


@router.post("/personal/execute")
async def personal_execute(
    payload: KernelExecuteInput,
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _personal_context(db, account, payload.conversation_id)
    return await _execute(db, context, payload)


@router.get("/personal/approvals")
async def personal_approvals(
    status: str | None = Query(default=None, max_length=30),
    limit: int = Query(default=50, ge=1, le=200),
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    filters = [
        KernelApproval.scope_kind == "personal",
        KernelApproval.owner_user_id == account.user.id,
    ]
    if status:
        filters.append(KernelApproval.status == status)
    rows = (
        await db.scalars(
            select(KernelApproval).where(*filters).order_by(KernelApproval.created_at.desc()).limit(limit)
        )
    ).all()
    return {"approvals": [approval_json(row, include_arguments=True) for row in rows]}


@router.post("/personal/approvals/{approval_id}/decision")
async def personal_approval_decision(
    approval_id: str,
    payload: ApprovalDecisionInput,
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _personal_context(db, account)
    try:
        row = await decide_approval(
            db,
            context=context,
            approval_id=approval_id,
            approved=payload.approved,
            decided_by_user_id=account.user.id,
        )
    except ApprovalError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.add(
        KernelEventRecord(
            event_type=f"approval.{row.status}",
            scope_kind="personal",
            workspace_id=None,
            owner_user_id=account.user.id,
            principal_id=context.principal_id,
            actor_type="human",
            actor_id=account.user.id,
            initiator_principal_id=row.requested_by_principal_id,
            executor_principal_id=f"user:{account.user.id}",
            capability_id=row.capability_id,
            resource_type="approval",
            resource_id=row.id,
            payload_json=json.dumps(
                {"approval_id": row.id, "status": row.status},
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
    )
    await db.commit()
    return approval_json(row, include_arguments=True)


@router.post("/personal/approvals/{approval_id}/execute")
async def execute_approved_personal_workspace_provision(
    approval_id: str,
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Execute a frozen, already-human-approved MCP provisioning request.

    This is invoked by DragonZpyder's same-origin browser after the owner
    approves an MCP-generated Kernel request. No client-supplied arguments,
    user IDs, request IDs, goal or Workspace authority are accepted here.
    Idempotency still belongs to the Kernel. Only workspace.create is enabled.
    """
    from packages.mcp.first_party import enabled as personal_mcp_enabled
    import os

    if (not personal_mcp_enabled()
            or os.getenv("OPERLY_PERSONAL_MCP_WORKSPACE_CREATE_ENABLED") != "1"):
        raise HTTPException(403, "Personal MCP workspace provisioning is unavailable")
    context = await _personal_context(db, account)
    try:
        row = await approval_for_context(
            db, context=context, approval_id=approval_id, lock=False,
        )
    except ApprovalError as error:
        raise HTTPException(404, "Personal approval unavailable") from error
    if (row.capability_id != "workspace.create" or row.status != "approved"
            or row.requested_by_user_id != account.user.id
            or row.requested_by_principal_id != context.principal_id
            or not row.request_id or not row.conversation_id):
        raise HTTPException(409, "Workspace approval is not executable")
    try:
        arguments = json.loads(row.arguments_json)
    except (ValueError, TypeError):
        raise HTTPException(409, "Workspace approval arguments are invalid") from None
    if not isinstance(arguments, dict):
        raise HTTPException(409, "Workspace approval arguments are invalid")
    # Reconstruct the same user/conversation context as the originating MCP
    # invocation, binding ownership from the authenticated browser session.
    execution_context = await _personal_context(db, account, row.conversation_id)
    runtime = build_personal_runtime()
    try:
        outcome = await runtime.execute(
            db, context=execution_context,
            request=RuntimeRequest(
                capability_id=row.capability_id,
                goal="",
                arguments=arguments,
                conversation_id=row.conversation_id,
                request_id=row.request_id,
                approval_id=row.id,
            ),
        )
    except RuntimeExecutionError as error:
        raise HTTPException(error.status_code, detail={
            "code": error.code, "message": str(error), "run_id": error.run_id,
        }) from error
    response = outcome.as_dict()
    created_id = (response.get("result") or {}).get("id")
    owned = await db.scalar(select(Tenant.id).join(
        TenantMember, TenantMember.tenant_id == Tenant.id,
    ).where(Tenant.id == created_id, TenantMember.user_id == account.user.id,
            TenantMember.role == "owner"))
    if owned != created_id:
        # Never retry an unknown create response; an owner can inspect the
        # workspace list to reconcile any partially successful operation.
        raise HTTPException(503, detail={
            "code": "WORKSPACE_READBACK_UNVERIFIED",
            "message": "Workspace execution returned but ownership readback failed.",
        })
    response["workspace_ownership_verified"] = True
    return response


@router.get("/personal/runs/{run_id}")
async def personal_run(
    run_id: str,
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    run = await db.scalar(
        select(KernelRun).where(
            KernelRun.id == run_id,
            KernelRun.scope_kind == "personal",
            KernelRun.owner_user_id == account.user.id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Runtime run not found")
    return await _run_payload(db, run)
