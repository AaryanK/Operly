from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.database.kernel_models import KernelApproval, KernelEventRecord, KernelRun, KernelRunStep
from packages.kernel.approvals import ApprovalError, approval_json, decide_approval
from packages.kernel.contracts import CapabilitySpec, RuntimeRequest
from packages.kernel.ingress import TrustedIngress, resolve_ingress_context
from packages.kernel.runtime import RuntimeExecutionError
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


router = APIRouter(prefix="/api/personal-tools", tags=["personal-tools"])
_runtime = build_personal_runtime()


class PersonalToolExecuteInput(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    goal: str = Field(default="", max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=160)
    request_id: str | None = Field(default=None, max_length=160)
    approval_id: str | None = Field(default=None, max_length=80)


class ApprovalDecisionInput(BaseModel):
    approved: bool


async def _personal_context(
    db: AsyncSession,
    auth: AccountAuthContext,
    conversation_id: str | None = None,
) -> ExecutionContext:
    return await resolve_ingress_context(
        db,
        TrustedIngress(
            scope_kind=ScopeKind.PERSONAL,
            user_id=auth.user.id,
            workspace_id=None,
            channel="web",
            surface=SurfaceKind.PERSONAL_PRIVATE,
            conversation_id=conversation_id,
            metadata={"ingress": "operly_personal_tools_web"},
        ),
    )


def personal_tool_endpoint(capability_id: str) -> str:
    return f"/personal-tools/{quote(capability_id, safe='')}/execute"


def personal_tool_contract_endpoint(capability_id: str) -> str:
    return f"/personal-tools/{quote(capability_id, safe='')}"


def _tool_summary(spec: CapabilitySpec) -> dict[str, Any]:
    """Discovery payload intentionally omits schemas until describe is requested."""
    return {
        "id": spec.id,
        "version": spec.version,
        "display_name": spec.display_name,
        "description": spec.description,
        "risk": spec.risk.value,
        "approval_required": spec.approval_required,
        "reversible": spec.reversible,
        "aliases": list(spec.aliases),
        "tags": sorted(spec.tags),
        "method": "POST",
        "endpoint": personal_tool_endpoint(spec.id),
        "contract_endpoint": personal_tool_contract_endpoint(spec.id),
    }


def _tool_contract(spec: CapabilitySpec) -> dict[str, Any]:
    return {
        **spec.public_dict(),
        "method": "POST",
        "endpoint": personal_tool_endpoint(spec.id),
        "contract_endpoint": personal_tool_contract_endpoint(spec.id),
    }


async def _available_tools(
    db: AsyncSession,
    context: ExecutionContext,
    query: str | None = None,
    limit: int = 50,
) -> tuple[CapabilitySpec, ...]:
    specs = await _runtime.available_capabilities(
        db,
        context=context,
        query=query,
        limit=limit,
    )
    return tuple(spec for spec in specs if "personal" in spec.scopes)


async def _available_tool(
    db: AsyncSession,
    context: ExecutionContext,
    capability_id: str,
) -> CapabilitySpec:
    normalized = str(capability_id or "").strip().lower()
    if not normalized:
        raise HTTPException(status_code=404, detail="Personal tool not found")
    for spec in await _available_tools(db, context, normalized, 50):
        if spec.id == normalized:
            return spec
    raise HTTPException(
        status_code=404,
        detail="Personal tool is unavailable, disconnected, or not authorized",
    )


async def _execute(
    db: AsyncSession,
    context: ExecutionContext,
    capability_id: str,
    payload: PersonalToolExecuteInput,
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


def _json(value: str | None) -> Any:
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
            scope_kind="personal",
            workspace_id=None,
            owner_user_id=context.user_id,
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
async def list_personal_tools(
    query: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _personal_context(db, auth)
    specs = await _available_tools(db, context, query, limit)
    return {
        "scope_kind": "personal",
        "owner_user_id": auth.user.id,
        "discovery_mode": "search_then_describe",
        "tools": [_tool_summary(spec) for spec in specs],
    }


@router.get("/approvals")
async def personal_approvals(
    status: str | None = Query(default=None, max_length=30),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    filters = [
        KernelApproval.scope_kind == "personal",
        KernelApproval.owner_user_id == auth.user.id,
        KernelApproval.workspace_id.is_(None),
    ]
    if status:
        filters.append(KernelApproval.status == status)
    rows = (
        await db.scalars(
            select(KernelApproval)
            .where(*filters)
            .order_by(KernelApproval.created_at.desc())
            .limit(limit)
        )
    ).all()
    return {"approvals": [approval_json(row, include_arguments=True) for row in rows]}


@router.post("/approvals/{approval_id}/decision")
async def personal_approval_decision(
    approval_id: str,
    payload: ApprovalDecisionInput,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _personal_context(db, auth)
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


@router.get("/runs/{run_id}")
async def personal_run(
    run_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    run = await db.scalar(
        select(KernelRun).where(
            KernelRun.id == run_id,
            KernelRun.scope_kind == "personal",
            KernelRun.owner_user_id == auth.user.id,
            KernelRun.workspace_id.is_(None),
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Personal tool run not found")
    return await _run_payload(db, run)


@router.get("/{capability_id}")
async def personal_tool_contract(
    capability_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _personal_context(db, auth)
    return _tool_contract(await _available_tool(db, context, capability_id))


@router.post("/{capability_id}/execute")
async def execute_personal_tool(
    capability_id: str,
    payload: PersonalToolExecuteInput,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _personal_context(db, auth, payload.conversation_id)
    return await _execute(db, context, capability_id, payload)
