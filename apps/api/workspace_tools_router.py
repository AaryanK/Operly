from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.kernel_models import KernelApproval, KernelEventRecord, KernelRun, KernelRunStep
from packages.kernel import RuntimeExecutionError, build_kernel_runtime
from packages.kernel.approvals import ApprovalError, approval_json, decide_approval
from packages.kernel.contracts import CapabilitySpec, RuntimeRequest
from packages.kernel.ingress import TrustedIngress, resolve_ingress_context
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


router = APIRouter(prefix="/api/workspace-tools", tags=["workspace-tools"])
_runtime = build_kernel_runtime()


class WorkspaceToolExecuteInput(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    goal: str = Field(default="", max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=160)
    request_id: str | None = Field(default=None, max_length=160)
    approval_id: str | None = Field(default=None, max_length=80)


class ApprovalDecisionInput(BaseModel):
    approved: bool


async def _workspace_context(
    db: AsyncSession,
    auth: AuthContext,
    conversation_id: str | None = None,
) -> ExecutionContext:
    return await resolve_ingress_context(
        db,
        TrustedIngress(
            scope_kind=ScopeKind.WORKSPACE,
            user_id=auth.user.id,
            workspace_id=auth.tenant.id,
            channel="web",
            surface=SurfaceKind.WORKSPACE_PRIVATE,
            conversation_id=conversation_id,
            metadata={"ingress": "operly_workspace_tools_web"},
        ),
    )


def workspace_tool_endpoint(capability_id: str) -> str:
    return f"/workspace-tools/{quote(capability_id, safe='')}/execute"


def workspace_tool_contract_endpoint(capability_id: str) -> str:
    return f"/workspace-tools/{quote(capability_id, safe='')}"


def _tool_json(spec: CapabilitySpec) -> dict[str, Any]:
    return {
        **spec.public_dict(),
        "method": "POST",
        "endpoint": workspace_tool_endpoint(spec.id),
        "contract_endpoint": workspace_tool_contract_endpoint(spec.id),
    }


async def _available_tools(
    db: AsyncSession,
    context: ExecutionContext,
    query: str | None = None,
) -> tuple[CapabilitySpec, ...]:
    specs = await _runtime.available_capabilities(
        db,
        context=context,
        query=query,
        limit=500 if not query else 50,
    )
    return tuple(spec for spec in specs if "workspace" in spec.scopes)


async def _available_tool(
    db: AsyncSession,
    context: ExecutionContext,
    capability_id: str,
) -> CapabilitySpec:
    normalized = str(capability_id or "").strip().lower()
    if not normalized:
        raise HTTPException(status_code=404, detail="Workspace tool not found")
    for spec in await _available_tools(db, context, normalized):
        if spec.id == normalized:
            return spec
    raise HTTPException(status_code=404, detail="Workspace tool is unavailable or not authorized")


async def _execute(
    db: AsyncSession,
    context: ExecutionContext,
    capability_id: str,
    payload: WorkspaceToolExecuteInput,
) -> dict[str, Any]:
    await _available_tool(db, context, capability_id)
    request = RuntimeRequest(
        goal=payload.goal,
        capability_id=capability_id,
        arguments=payload.arguments,
        conversation_id=payload.conversation_id,
        request_id=payload.request_id,
        approval_id=payload.approval_id,
    )
    try:
        response = await _runtime.execute(db, context=context, request=request)
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
        "workspace_id": run.workspace_id,
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


def _approval_event(
    db: AsyncSession,
    *,
    context: ExecutionContext,
    approval: KernelApproval,
    actor_user_id: str,
) -> None:
    db.add(
        KernelEventRecord(
            event_type=f"approval.{approval.status}",
            scope_kind="workspace",
            workspace_id=context.workspace_id,
            owner_user_id=None,
            principal_id=context.principal_id,
            actor_type="human",
            actor_id=actor_user_id,
            initiator_principal_id=approval.requested_by_principal_id,
            executor_principal_id=f"user:{actor_user_id}",
            capability_id=approval.capability_id,
            resource_type="approval",
            resource_id=approval.id,
            payload_json=json.dumps(
                {"approval_id": approval.id, "status": approval.status},
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
    )


@router.get("")
async def list_workspace_tools(
    query: str | None = Query(default=None, max_length=200),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _workspace_context(db, auth)
    specs = await _available_tools(db, context, query)
    return {
        "scope_kind": "workspace",
        "workspace_id": auth.tenant.id,
        "workspace_mode": context.workspace_mode,
        "tools": [_tool_json(spec) for spec in specs],
    }


@router.get("/approvals")
async def workspace_approvals(
    status: str | None = Query(default=None, max_length=30),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _workspace_context(db, auth)
    if not context.can("actions:read") and not context.can("actions:approve"):
        raise HTTPException(status_code=403, detail="Approval access denied")
    filters = [KernelApproval.scope_kind == "workspace", KernelApproval.workspace_id == auth.tenant.id]
    if status:
        filters.append(KernelApproval.status == status)
    rows = (
        await db.scalars(
            select(KernelApproval).where(*filters).order_by(KernelApproval.created_at.desc()).limit(limit)
        )
    ).all()
    return {"approvals": [approval_json(row, include_arguments=True) for row in rows]}


@router.post("/approvals/{approval_id}/decision")
async def workspace_approval_decision(
    approval_id: str,
    payload: ApprovalDecisionInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _workspace_context(db, auth)
    if not context.can("actions:approve"):
        raise HTTPException(status_code=403, detail="Approval permission denied")
    try:
        row = await decide_approval(
            db,
            context=context,
            approval_id=approval_id,
            approved=payload.approved,
            decided_by_user_id=auth.user.id,
        )
    except ApprovalError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _approval_event(db, context=context, approval=row, actor_user_id=auth.user.id)
    await db.commit()
    return approval_json(row, include_arguments=True)


@router.get("/events")
async def workspace_events(
    event_type: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _workspace_context(db, auth)
    if not context.can("actions:read"):
        raise HTTPException(status_code=403, detail="Event audit access denied")
    filters = [KernelEventRecord.workspace_id == auth.tenant.id]
    if event_type:
        filters.append(KernelEventRecord.event_type == event_type)
    rows = (
        await db.scalars(
            select(KernelEventRecord)
            .where(*filters)
            .order_by(KernelEventRecord.created_at.desc())
            .limit(limit)
        )
    ).all()
    return {
        "events": [
            {
                "id": row.id,
                "event_type": row.event_type,
                "principal_id": row.principal_id,
                "actor_type": row.actor_type,
                "actor_id": row.actor_id,
                "initiator_principal_id": row.initiator_principal_id,
                "executor_principal_id": row.executor_principal_id,
                "capability_id": row.capability_id,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "payload": _json(row.payload_json),
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }


@router.get("/runs/{run_id}")
async def workspace_run(
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    run = await db.scalar(
        select(KernelRun).where(
            KernelRun.id == run_id,
            KernelRun.scope_kind == "workspace",
            KernelRun.workspace_id == auth.tenant.id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Workspace tool run not found")
    return await _run_payload(db, run)


@router.get("/{capability_id}")
async def workspace_tool_contract(
    capability_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _workspace_context(db, auth)
    return _tool_json(await _available_tool(db, context, capability_id))


@router.post("/{capability_id}/execute")
async def execute_workspace_tool(
    capability_id: str,
    payload: WorkspaceToolExecuteInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _workspace_context(db, auth, payload.conversation_id)
    return await _execute(db, context, capability_id, payload)
