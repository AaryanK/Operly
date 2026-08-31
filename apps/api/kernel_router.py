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
from packages.kernel.approvals import ApprovalError, approval_json, decide_approval
from packages.kernel.contracts import RuntimeRequest
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
