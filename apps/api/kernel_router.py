from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import (
    AccountAuthContext,
    AuthContext,
    get_account_auth_context,
    get_auth_context,
    get_db,
)
from packages.database.kernel_models import KernelEventRecord, KernelRun, KernelRunStep
from packages.kernel import RuntimeExecutionError, build_kernel_runtime
from packages.kernel.contracts import RuntimeRequest
from packages.kernel.ingress import TrustedIngress, resolve_ingress_context
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind

router = APIRouter(prefix="/api/kernel", tags=["kernel"])
_runtime = build_kernel_runtime()


class KernelExecuteInput(BaseModel):
    goal: str = Field(default="", max_length=4000)
    capability_id: str | None = Field(default=None, max_length=160)
    arguments: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str | None = Field(default=None, max_length=160)
    request_id: str | None = Field(default=None, max_length=160)


async def _workspace_context(db: AsyncSession, auth: AuthContext, conversation_id: str | None = None) -> ExecutionContext:
    return await resolve_ingress_context(
        db,
        TrustedIngress(
            scope_kind=ScopeKind.WORKSPACE,
            user_id=auth.user.id,
            workspace_id=auth.tenant.id,
            channel="web",
            surface=SurfaceKind.WORKSPACE_PRIVATE,
            conversation_id=conversation_id,
            metadata={"ingress": "operly_web_kernel"},
        ),
    )


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
            metadata={"ingress": "operly_web_kernel"},
        ),
    )


def _capabilities(context: ExecutionContext, query: str | None) -> list[dict[str, Any]]:
    if query:
        specs = _runtime.registry.search(query, context=context, effective_only=True, limit=30)
    else:
        specs = _runtime.registry.effective(context)
    return [spec.public_dict() for spec in specs]


def _runtime_request(payload: KernelExecuteInput) -> RuntimeRequest:
    return RuntimeRequest(
        goal=payload.goal,
        capability_id=payload.capability_id,
        arguments=payload.arguments,
        conversation_id=payload.conversation_id,
        request_id=payload.request_id,
    )


async def _execute(db: AsyncSession, context: ExecutionContext, payload: KernelExecuteInput):
    try:
        response = await _runtime.execute(db, context=context, request=_runtime_request(payload))
    except RuntimeExecutionError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error), "run_id": error.run_id},
        ) from error
    return response.as_dict()


@router.get("/capabilities")
async def workspace_capabilities(
    query: str | None = Query(default=None, max_length=200),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _workspace_context(db, auth)
    return {
        "scope_kind": context.scope_kind.value,
        "workspace_id": context.workspace_id,
        "workspace_mode": context.workspace_mode,
        "capabilities": _capabilities(context, query),
    }


@router.post("/execute")
async def workspace_execute(
    payload: KernelExecuteInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _workspace_context(db, auth, payload.conversation_id)
    return await _execute(db, context, payload)


@router.get("/personal/capabilities")
async def personal_capabilities(
    query: str | None = Query(default=None, max_length=200),
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _personal_context(db, account)
    return {
        "scope_kind": context.scope_kind.value,
        "user_id": context.user_id,
        "capabilities": _capabilities(context, query),
    }


@router.post("/personal/execute")
async def personal_execute(
    payload: KernelExecuteInput,
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    context = await _personal_context(db, account, payload.conversation_id)
    return await _execute(db, context, payload)


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
        raise HTTPException(status_code=404, detail="Runtime run not found")
    return await _run_payload(db, run)


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
