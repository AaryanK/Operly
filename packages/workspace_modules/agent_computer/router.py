from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.agent_computer_models import AgentComputerSessionRecord, AgentComputerStepRecord
from packages.kernel.approvals import ApprovalError, decide_approval
from packages.kernel.contracts import CapabilitySpec, RuntimeRequest
from packages.kernel.ingress import TrustedIngress, resolve_ingress_context
from packages.kernel.runtime import RuntimeExecutionError
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime


router = APIRouter(prefix="/api/agent-computer", tags=["agent-computer"])
_runtime = build_workspace_runtime()

ACTION_CAPABILITIES: dict[str, str] = {
    "inspect": "studio.project.inspect",
    "deploy": "studio.solution.deploy",
    "rollback": "studio.solution.rollback",
    "domain": "studio.solution.domain.request",
}


class CreateComputerSessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["inspect", "deploy", "rollback", "domain"]
    objective: str = Field(default="", max_length=4000)
    project_id: str | None = Field(default=None, max_length=80)
    solution_id: str | None = Field(default=None, max_length=80)
    deployment_id: str | None = Field(default=None, max_length=80)
    domain: str | None = Field(default=None, max_length=253)
    solution_name: str | None = Field(default=None, max_length=200)


async def _context(
    db: AsyncSession,
    auth: AuthContext,
    *,
    session_id: str | None = None,
) -> ExecutionContext:
    context = await resolve_ingress_context(
        db,
        TrustedIngress(
            scope_kind=ScopeKind.WORKSPACE,
            user_id=auth.user.id,
            workspace_id=auth.tenant.id,
            channel="web",
            surface=SurfaceKind.WORKSPACE_PRIVATE,
            conversation_id=f"agent-computer:{session_id}" if session_id else None,
            metadata={"ingress": "operly_agent_computer"},
        ),
    )
    if not context.can("computer:execute"):
        raise HTTPException(status_code=403, detail="Agent Computer permission denied")
    return context


async def _available_specs(db: AsyncSession, context: ExecutionContext) -> dict[str, CapabilitySpec]:
    specs = await _runtime.available_capabilities(db, context=context, query="studio", limit=100)
    return {spec.id: spec for spec in specs if spec.resource_scope == "workspace"}


def _arguments(payload: CreateComputerSessionInput) -> dict[str, Any]:
    if payload.action in {"inspect", "deploy"}:
        if not payload.project_id:
            raise HTTPException(status_code=422, detail="project_id is required for this Agent Computer task")
        args: dict[str, Any] = {"project_id": payload.project_id}
        if payload.action == "deploy" and payload.solution_name:
            args["solution_name"] = payload.solution_name
        return args
    if payload.action == "rollback":
        if not payload.solution_id:
            raise HTTPException(status_code=422, detail="solution_id is required for rollback")
        args = {"solution_id": payload.solution_id}
        if payload.deployment_id:
            args["deployment_id"] = payload.deployment_id
        return args
    if not payload.solution_id or not payload.domain:
        raise HTTPException(status_code=422, detail="solution_id and domain are required for a domain request")
    return {"solution_id": payload.solution_id, "domain": payload.domain.strip().lower()}


def _safe_json(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


async def _steps(db: AsyncSession, session_id: str) -> list[AgentComputerStepRecord]:
    return list(
        (
            await db.scalars(
                select(AgentComputerStepRecord)
                .where(AgentComputerStepRecord.session_id == session_id)
                .order_by(AgentComputerStepRecord.sequence, AgentComputerStepRecord.created_at)
            )
        ).all()
    )


def _step_json(row: AgentComputerStepRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "sequence": row.sequence,
        "kind": row.kind,
        "status": row.status,
        "capability_id": row.capability_id,
        "request_id": row.request_id,
        "run_id": row.run_id,
        "approval_id": row.approval_id,
        "summary": row.summary,
        "payload": _safe_json(row.payload_json),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _session_json(
    db: AsyncSession,
    row: AgentComputerSessionRecord,
    *,
    include_steps: bool = True,
) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "title": row.title,
        "objective": row.objective,
        "action": row.action,
        "state": row.state,
        "project_id": row.project_id,
        "solution_id": row.solution_id,
        "arguments": _safe_json(row.arguments_json),
        "result": _safe_json(row.result_json),
        "current_capability_id": row.current_capability_id,
        "current_request_id": row.current_request_id,
        "current_run_id": row.current_run_id,
        "approval_id": row.approval_id,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }
    if include_steps:
        payload["steps"] = [_step_json(step) for step in await _steps(db, row.id)]
    return payload


async def _owned_session(
    db: AsyncSession,
    auth: AuthContext,
    session_id: str,
) -> AgentComputerSessionRecord:
    row = await db.scalar(
        select(AgentComputerSessionRecord).where(
            AgentComputerSessionRecord.id == session_id,
            AgentComputerSessionRecord.tenant_id == auth.tenant.id,
            AgentComputerSessionRecord.user_id == auth.user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Agent Computer session not found")
    return row


async def _append_step(
    db: AsyncSession,
    row: AgentComputerSessionRecord,
    *,
    kind: str,
    status: str,
    summary: str,
    capability_id: str | None = None,
    request_id: str | None = None,
    run_id: str | None = None,
    approval_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AgentComputerStepRecord:
    sequence = int(
        (await db.scalar(select(func.max(AgentComputerStepRecord.sequence)).where(AgentComputerStepRecord.session_id == row.id)))
        or 0
    ) + 1
    step = AgentComputerStepRecord(
        tenant_id=row.tenant_id,
        session_id=row.id,
        sequence=sequence,
        kind=kind,
        status=status,
        capability_id=capability_id,
        request_id=request_id,
        run_id=run_id,
        approval_id=approval_id,
        summary=summary[:2000],
        payload_json=json.dumps(payload or {}, separators=(",", ":"), sort_keys=True, default=str),
    )
    db.add(step)
    await db.flush()
    return step


async def _execute_session(
    db: AsyncSession,
    auth: AuthContext,
    row: AgentComputerSessionRecord,
    *,
    resume: bool,
) -> dict[str, Any]:
    context = await _context(db, auth, session_id=row.id)
    capability_id = ACTION_CAPABILITIES[row.action]
    specs = await _available_specs(db, context)
    if capability_id not in specs:
        row.state = "failed"
        row.error = f"{capability_id} is not currently authorized or available"
        await _append_step(
            db,
            row,
            kind="authority",
            status="blocked",
            summary=row.error,
            capability_id=capability_id,
        )
        await db.commit()
        return await _session_json(db, row)

    args = _safe_json(row.arguments_json)
    request_id = row.current_request_id or f"agent-computer:{row.id}:{uuid4()}"
    approval_id = row.approval_id if resume else None
    row.current_capability_id = capability_id
    row.current_request_id = request_id
    row.state = "running"
    row.error = None
    call_step = await _append_step(
        db,
        row,
        kind="tool_call",
        status="running",
        summary=("Resume approved exact invocation" if resume else "Submit governed Workspace capability"),
        capability_id=capability_id,
        request_id=request_id,
        approval_id=approval_id,
        payload={"arguments": args},
    )
    await db.commit()

    try:
        response = await _runtime.execute(
            db,
            context=context,
            request=RuntimeRequest(
                goal=row.objective,
                capability_id=capability_id,
                arguments=args,
                conversation_id=f"agent-computer:{row.id}",
                request_id=request_id,
                approval_id=approval_id,
            ),
        )
    except RuntimeExecutionError as error:
        row.current_run_id = error.run_id
        call_step.run_id = error.run_id
        if error.code == "approval_required" and error.approval_id:
            row.state = "waiting_for_approval"
            row.approval_id = error.approval_id
            call_step.status = "waiting_for_approval"
            call_step.approval_id = error.approval_id
            await _append_step(
                db,
                row,
                kind="approval",
                status="pending",
                summary="Human approval is required for this exact Workspace capability invocation.",
                capability_id=capability_id,
                request_id=request_id,
                run_id=error.run_id,
                approval_id=error.approval_id,
                payload={"arguments": args},
            )
        else:
            row.state = "failed"
            row.error = str(error)
            row.completed_at = datetime.utcnow()
            call_step.status = "failed"
            await _append_step(
                db,
                row,
                kind="tool_result",
                status="failed",
                summary=str(error),
                capability_id=capability_id,
                request_id=request_id,
                run_id=error.run_id,
                payload={"code": error.code},
            )
        await db.commit()
        return await _session_json(db, row)

    row.state = "completed"
    row.current_run_id = response.run_id
    row.approval_id = None
    row.result_json = json.dumps(response.result or {}, separators=(",", ":"), sort_keys=True, default=str)
    row.completed_at = datetime.utcnow()
    call_step.status = "completed"
    call_step.run_id = response.run_id
    await _append_step(
        db,
        row,
        kind="tool_result",
        status="completed",
        summary="Workspace capability completed and returned a validated result.",
        capability_id=capability_id,
        request_id=request_id,
        run_id=response.run_id,
        payload={"result": response.result or {}},
    )
    await db.commit()
    return await _session_json(db, row)


@router.get("/status")
async def agent_computer_status(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _context(db, auth)
    specs = await _available_specs(db, context)
    return {
        "enabled": True,
        "planner": "deterministic",
        "ai_enabled": False,
        "shell_access": False,
        "browser_credentials": False,
        "actions": [
            {
                "id": action,
                "capability_id": capability_id,
                "available": capability_id in specs,
                "risk": specs[capability_id].risk.value if capability_id in specs else None,
                "approval_required": specs[capability_id].approval_required if capability_id in specs else None,
            }
            for action, capability_id in ACTION_CAPABILITIES.items()
        ],
    }


@router.get("/catalog")
async def agent_computer_catalog(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _context(db, auth)
    specs = await _available_specs(db, context)
    capability_id = "studio.projects.list"
    if capability_id not in specs:
        raise HTTPException(status_code=403, detail="Studio project catalog is unavailable")
    try:
        response = await _runtime.execute(
            db,
            context=context,
            request=RuntimeRequest(
                capability_id=capability_id,
                arguments={"limit": 100},
                conversation_id="agent-computer:catalog",
                request_id=f"agent-computer-catalog:{auth.user.id}:{uuid4()}",
            ),
        )
    except RuntimeExecutionError as error:
        raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": str(error)}) from error
    return response.result or {"projects": []}


@router.get("/sessions")
async def list_agent_computer_sessions(
    limit: int = Query(default=30, ge=1, le=100),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _context(db, auth)
    rows = (
        await db.scalars(
            select(AgentComputerSessionRecord)
            .where(
                AgentComputerSessionRecord.tenant_id == auth.tenant.id,
                AgentComputerSessionRecord.user_id == auth.user.id,
            )
            .order_by(desc(AgentComputerSessionRecord.updated_at))
            .limit(limit)
        )
    ).all()
    return {"sessions": [await _session_json(db, row, include_steps=False) for row in rows]}


@router.post("/sessions", status_code=201)
async def create_agent_computer_session(
    payload: CreateComputerSessionInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _context(db, auth)
    specs = await _available_specs(db, context)
    capability_id = ACTION_CAPABILITIES[payload.action]
    if capability_id not in specs:
        raise HTTPException(status_code=403, detail=f"{capability_id} is not currently authorized or available")
    args = _arguments(payload)
    title = {
        "inspect": "Inspect Studio project",
        "deploy": "Deploy Studio solution",
        "rollback": "Roll back Studio solution",
        "domain": "Request Studio domain",
    }[payload.action]
    row = AgentComputerSessionRecord(
        tenant_id=auth.tenant.id,
        user_id=auth.user.id,
        principal_id=context.principal_id,
        title=title,
        objective=payload.objective.strip() or title,
        action=payload.action,
        state="ready",
        project_id=payload.project_id,
        solution_id=payload.solution_id,
        arguments_json=json.dumps(args, separators=(",", ":"), sort_keys=True),
        current_capability_id=capability_id,
    )
    db.add(row)
    await db.flush()
    await _append_step(
        db,
        row,
        kind="objective",
        status="ready",
        summary=row.objective,
        capability_id=capability_id,
        payload={"action": payload.action, "arguments": args},
    )
    await db.commit()
    return await _session_json(db, row)


@router.get("/sessions/{session_id}")
async def get_agent_computer_session(
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _context(db, auth, session_id=session_id)
    return await _session_json(db, await _owned_session(db, auth, session_id))


@router.post("/sessions/{session_id}/run")
async def run_agent_computer_session(
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await _owned_session(db, auth, session_id)
    if row.state == "completed":
        return await _session_json(db, row)
    if row.state == "waiting_for_approval":
        raise HTTPException(status_code=409, detail="This Agent Computer session is waiting for approval")
    if row.state == "cancelled":
        raise HTTPException(status_code=409, detail="Cancelled Agent Computer sessions cannot run")
    return await _execute_session(db, auth, row, resume=False)


@router.post("/sessions/{session_id}/resume")
async def resume_agent_computer_session(
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await _owned_session(db, auth, session_id)
    if row.state != "waiting_for_approval" or not row.approval_id:
        raise HTTPException(status_code=409, detail="Agent Computer session is not waiting for an approval")
    return await _execute_session(db, auth, row, resume=True)


@router.post("/sessions/{session_id}/cancel")
async def cancel_agent_computer_session(
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await _owned_session(db, auth, session_id)
    context = await _context(db, auth, session_id=session_id)
    if row.state in {"completed", "cancelled"}:
        return await _session_json(db, row)
    if row.approval_id:
        try:
            await decide_approval(
                db,
                context=context,
                approval_id=row.approval_id,
                approved=False,
                decided_by_user_id=auth.user.id,
            )
        except ApprovalError:
            pass
    row.state = "cancelled"
    row.completed_at = datetime.utcnow()
    await _append_step(
        db,
        row,
        kind="session",
        status="cancelled",
        summary="Agent Computer session cancelled by the human operator.",
        capability_id=row.current_capability_id,
        request_id=row.current_request_id,
        run_id=row.current_run_id,
        approval_id=row.approval_id,
    )
    row.approval_id = None
    await db.commit()
    return await _session_json(db, row)
