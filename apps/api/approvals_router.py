import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from apps.api.schemas import ApprovalDecision
from packages.actions.service import ActionService
from packages.capabilities.agent_harness import ROLE_AUTHORITY
from packages.capabilities.defaults import default_registry
from packages.database.models import Approval
from packages.database.product_models import SolutionImprovementProposal
from packages.tasks.runtime import resume_task_after_approval

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("")
async def list_approvals(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(Approval)
            .where(Approval.tenant_id == auth.tenant.id)
            .order_by(desc(Approval.created_at))
        )
    ).all()
    return [
        {
            "id": row.id,
            "action": row.action,
            "status": row.status,
            "details": json.loads(row.payload_json or "{}"),
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


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
            Approval.tenant_id == auth.tenant.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Approval not found")

    details = json.loads(row.payload_json or "{}")
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
                )
            else:
                await service.reject(auth.tenant.id, business_action_id)
                await resume_task_after_approval(db, approval_id, approved=False)
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
