import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import (
    AccountAuthContext,
    AuthContext,
    get_account_auth_context,
    get_auth_context,
    get_db,
)
from apps.api.schemas import ApprovalDecision
from packages.actions.service import ActionService
from packages.capabilities.agent_harness import ROLE_AUTHORITY
from packages.capabilities.defaults import default_registry
from packages.database.company_models import BusinessActionRecord
from packages.database.models import Approval
from packages.database.product_models import SolutionImprovementProposal
from packages.tasks.runtime import resume_task_after_approval

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


def _json_object(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _serialize_approval(db: AsyncSession, row: Approval) -> dict:
    payload = _json_object(row.payload_json)
    details = dict(payload)
    action_id = str(payload.get("business_action_id") or "").strip()
    action = await db.get(BusinessActionRecord, action_id) if action_id else None
    if action and (
        action.scope_kind == row.scope_kind
        and action.tenant_id == row.tenant_id
        and action.owner_user_id == row.owner_user_id
    ):
        action_arguments = _json_object(action.arguments_json)
        details.update(
            {
                "business_action_id": action.id,
                "objective": action.objective,
                "capability": action.capability,
                "arguments": action_arguments,
                "rationale": action.rationale,
                "expected_outcome": action.expected_outcome,
                "risk_level": action.risk_level,
                "provider": action.provider,
                "policy_decision": action.policy_decision,
                "origin": action.origin,
                "connector_id": action.connector_id,
                "resource_type": action.resource_type,
                "action_status": str(action.status),
                "scope_kind": action.scope_kind,
            }
        )
    return {
        "id": row.id,
        "action": row.action,
        "status": row.status,
        "scope_kind": row.scope_kind,
        "details": details,
        "payload": payload,
        "created_at": row.created_at.isoformat(),
    }


async def _serialize_rows(db: AsyncSession, rows: list[Approval]) -> list[dict]:
    return [await _serialize_approval(db, row) for row in rows]


@router.get("")
async def list_approvals(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(Approval)
            .where(
                Approval.scope_kind == "workspace",
                Approval.tenant_id == auth.tenant.id,
                Approval.owner_user_id.is_(None),
            )
            .order_by(desc(Approval.created_at))
        )
    ).all()
    return await _serialize_rows(db, list(rows))


@router.get("/personal")
async def list_personal_approvals(
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(Approval)
            .where(
                Approval.scope_kind == "personal",
                Approval.tenant_id.is_(None),
                Approval.owner_user_id == auth.user.id,
            )
            .order_by(desc(Approval.created_at))
        )
    ).all()
    return await _serialize_rows(db, list(rows))


@router.patch("/personal/{approval_id}")
async def decide_personal_approval(
    approval_id: str,
    payload: ApprovalDecision,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    if payload.status not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="Invalid decision")

    row = await db.scalar(
        select(Approval).where(
            Approval.id == approval_id,
            Approval.scope_kind == "personal",
            Approval.tenant_id.is_(None),
            Approval.owner_user_id == auth.user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Approval not found")

    details = _json_object(row.payload_json)
    business_action_id = details.get("business_action_id")
    task_resumed = 0
    if business_action_id:
        service = ActionService(
            db,
            default_registry(),
            authority=set(ROLE_AUTHORITY.get("owner", set())),
            actor_id=auth.user.id,
        )
        try:
            if payload.status == "approved":
                action = await service.approve_personal(auth.user.id, business_action_id)
                task_resumed = await resume_task_after_approval(
                    db,
                    approval_id,
                    approved=str(action.status) == "VERIFIED",
                )
            else:
                await service.reject_personal(auth.user.id, business_action_id)
                task_resumed = await resume_task_after_approval(
                    db,
                    approval_id,
                    approved=False,
                )
        except (LookupError, ValueError, PermissionError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    else:
        row.status = payload.status

    await db.commit()
    return {
        "ok": True,
        "business_action_id": business_action_id,
        "task_resumed": task_resumed,
    }


@router.patch("/{approval_id}")
async def decide_approval(
    approval_id: str,
    payload: ApprovalDecision,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    if payload.status not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="Invalid decision")

    row = await db.scalar(
        select(Approval).where(
            Approval.id == approval_id,
            Approval.scope_kind == "workspace",
            Approval.tenant_id == auth.tenant.id,
            Approval.owner_user_id.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Approval not found")

    details = _json_object(row.payload_json)
    business_action_id = details.get("business_action_id")
    task_resumed = 0
    if business_action_id:
        service = ActionService(
            db,
            default_registry(),
            authority=set(ROLE_AUTHORITY.get(auth.role, set())),
            actor_id=auth.user.id,
        )
        try:
            if payload.status == "approved":
                action = await service.approve(auth.tenant.id, business_action_id)
                task_resumed = await resume_task_after_approval(
                    db,
                    approval_id,
                    approved=str(action.status) == "VERIFIED",
                    tenant_id=auth.tenant.id,
                )
            else:
                await service.reject(auth.tenant.id, business_action_id)
                await resume_task_after_approval(
                    db,
                    approval_id,
                    approved=False,
                    tenant_id=auth.tenant.id,
                )
                proposal = await db.scalar(
                    select(SolutionImprovementProposal).where(
                        SolutionImprovementProposal.tenant_id == auth.tenant.id,
                        SolutionImprovementProposal.action_id == business_action_id,
                    )
                )
                if proposal:
                    proposal.status = "rejected"
        except (LookupError, ValueError, PermissionError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    else:
        row.status = payload.status

    await db.commit()
    return {
        "ok": True,
        "business_action_id": business_action_id,
        "task_resumed": task_resumed,
    }
