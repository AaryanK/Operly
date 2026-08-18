import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.actions.service import ActionService
from packages.business_brain import AgentInput, get_agent_service
from packages.capabilities.providers import default_registry
from packages.company.events import query_events
from packages.company.state import get_company_state
from packages.database.company_models import BusinessActionRecord
from packages.database.business_models import Quote
from packages.company.attention import attention_items

router = APIRouter(prefix="/api", tags=["company operating system"])


class PlanInput(BaseModel):
    objective: str = Field(min_length=3, max_length=1000)


def action_payload(row):
    return {"id": row.id, "tenant_id": row.tenant_id, "objective": row.objective, "capability": row.capability,
            "provider": row.provider, "arguments": json.loads(row.arguments_json), "rationale": row.rationale,
            "expected_outcome": row.expected_outcome, "risk_level": row.risk_level, "status": row.status,
            "policy_decision": row.policy_decision, "approval_id": row.approval_id, "result": json.loads(row.result_json),
            "verification": json.loads(row.verification_json), "correlation_id": row.correlation_id,
            "causation_id": row.causation_id, "idempotency_key": row.idempotency_key,
            "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}


@router.get("/company/state")
async def company_state(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    return (await get_company_state(auth.tenant.id, db)).to_dict()

@router.get("/company/attention")
async def company_attention(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    return await attention_items(db, auth.tenant.id)

@router.get("/home/command-center")
async def home_command_center(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    state=await get_company_state(auth.tenant.id,db)
    quote_value=await db.scalar(select(func.coalesce(func.sum(Quote.total),0)).where(Quote.tenant_id==auth.tenant.id,Quote.status.in_(["draft","sent","pending"])))
    return {"summary":{**state.metrics,"open_opportunities":sum(v for k,v in state.operations["leads_by_stage"].items() if k not in {"won","lost"}),"open_tasks":len(state.operations["open_tasks"]),"outstanding_quote_value":float(quote_value or 0)},"attention":state.attention,"recent_actions":state.operations["recent_actions"][:8]}


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
    # Compatibility endpoint: reasoning now runs in the same persistent model/tool loop as Home chat.
    return await get_agent_service().run(AgentInput(tenant_id=auth.tenant.id,principal_id=f"web-user:{auth.user.id}",
        actor_name=auth.user.display_name,channel="web",text=payload.objective,
        metadata={"user_id":auth.user.id,"role":auth.role,"entrypoint":"company_plan"}))


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
