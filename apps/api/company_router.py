import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.actions.planner import plan_business_objective
from packages.actions.service import ActionService
from packages.capabilities.providers import default_registry
from packages.company.events import query_events
from packages.company.state import get_company_state
from packages.database.company_models import BusinessActionRecord

router = APIRouter(prefix="/api", tags=["company operating system"])


class PlanInput(BaseModel):
    objective: str = Field(min_length=3, max_length=1000)


def action_payload(row):
    return {"id": row.id, "tenant_id": row.tenant_id, "objective": row.objective, "capability": row.capability,
            "provider": row.provider, "arguments": json.loads(row.arguments_json), "rationale": row.rationale,
            "expected_outcome": row.expected_outcome, "risk_level": row.risk_level, "status": row.status,
            "policy_decision": row.policy_decision, "approval_id": row.approval_id, "result": json.loads(row.result_json),
            "verification": json.loads(row.verification_json), "correlation_id": row.correlation_id,
            "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}


@router.get("/company/state")
async def company_state(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    return (await get_company_state(auth.tenant.id, db)).to_dict()


@router.get("/company/events")
async def company_events(event_type: str | None = None, correlation_id: str | None = None,
                         since: datetime | None = None, until: datetime | None = None,
                         limit: int = Query(100, ge=1, le=500), auth: AuthContext = Depends(get_auth_context),
                         db: AsyncSession = Depends(get_db)):
    events = await query_events(db, auth.tenant.id, event_type=event_type, correlation_id=correlation_id,
                                since=since, until=until, limit=limit)
    return [{"id": x.id, "tenant_id": x.tenant_id, "event_type": x.event_type, "occurred_at": x.occurred_at.isoformat(),
             "actor_type": x.actor_type, "actor_id": x.actor_id, "source": x.source, "payload": x.payload,
             "correlation_id": x.correlation_id, "causation_id": x.causation_id, "metadata": x.metadata} for x in events]


@router.post("/company/plan")
async def create_plan(payload: PlanInput, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    registry = default_registry(); plan = await plan_business_objective(auth.tenant.id, payload.objective, db, registry)
    service = ActionService(db, registry); actions = []
    for node in plan["nodes"]:
        if node["implementation_mode"] == "existing_capability":
            actions.append(await service.propose(tenant_id=auth.tenant.id, objective=payload.objective,
                capability=node["capability"], arguments=node["arguments"], rationale=node["rationale"],
                expected_outcome=node["expected_outcome"], risk_level=node["risk_level"]))
    await db.commit()
    return {**plan, "actions": [action_payload(row) for row in actions]}


@router.get("/actions")
async def actions(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(BusinessActionRecord).where(BusinessActionRecord.tenant_id == auth.tenant.id)
                             .order_by(BusinessActionRecord.created_at.desc()))).all()
    return [action_payload(row) for row in rows]


async def _decide(action_id, approve, auth, db):
    service = ActionService(db, default_registry())
    try: row = await (service.approve(auth.tenant.id, action_id) if approve else service.reject(auth.tenant.id, action_id))
    except LookupError as error: raise HTTPException(404, str(error)) from error
    except ValueError as error: raise HTTPException(409, str(error)) from error
    await db.commit(); return action_payload(row)


@router.post("/actions/{action_id}/approve")
async def approve_action(action_id: str, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    return await _decide(action_id, True, auth, db)


@router.post("/actions/{action_id}/reject")
async def reject_action(action_id: str, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    return await _decide(action_id, False, auth, db)
