import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.actions.service import ActionService
from packages.business_brain import AgentInput, get_agent_service
from packages.capabilities.agent_harness import ROLE_AUTHORITY
from packages.capabilities.defaults import default_registry
from packages.company.attention import attention_items
from packages.company.events import query_events
from packages.company.intelligence import (
    answer_question,
    generate_questions,
    observe_evidence,
    profile_payload,
    synthesize_profile,
)
from packages.company.research import research_company
from packages.company.state import get_company_state
from packages.database.business_models import Quote
from packages.database.company_models import BusinessActionRecord
from packages.database.product_models import CompanyEvidence, CompanyProfile

router = APIRouter(prefix="/api", tags=["company operating system"])


class PlanInput(BaseModel):
    objective: str = Field(min_length=3, max_length=1000)


class DiscoverInput(BaseModel):
    business: str = Field(min_length=2, max_length=2000)
    max_pages: int = Field(default=5, ge=1, le=10)


class AnswerInput(BaseModel):
    question_id: str
    answer: object


class ProfilePatch(BaseModel):
    fields: dict[str, object]


def action_payload(row):
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "objective": row.objective,
        "capability": row.capability,
        "provider": row.provider,
        "arguments": json.loads(row.arguments_json),
        "rationale": row.rationale,
        "expected_outcome": row.expected_outcome,
        "risk_level": row.risk_level,
        "status": row.status,
        "policy_decision": row.policy_decision,
        "approval_id": row.approval_id,
        "result": json.loads(row.result_json),
        "verification": json.loads(row.verification_json),
        "correlation_id": row.correlation_id,
        "causation_id": row.causation_id,
        "idempotency_key": row.idempotency_key,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("/company/state")
async def company_state(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return (await get_company_state(auth.tenant.id, db)).to_dict()


@router.post("/company/discover")
async def discover_company(
    payload: DiscoverInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    result = await research_company(
        db,
        auth.tenant.id,
        payload.business,
        max_pages=payload.max_pages,
    )
    await db.commit()
    return result


@router.get("/company/profile")
async def get_profile(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return profile_payload(await db.get(CompanyProfile, auth.tenant.id))


@router.get("/company/evidence")
async def get_evidence(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(CompanyEvidence)
            .where(CompanyEvidence.tenant_id == auth.tenant.id)
            .order_by(CompanyEvidence.observed_at.desc())
            .limit(250)
        )
    ).all()
    return [
        {
            "id": row.id,
            "field": row.field_key,
            "value": json.loads(row.value_json),
            "source_type": row.source_type,
            "source_url": row.source_url,
            "source_reference": row.source_reference,
            "confidence": row.confidence,
            "observed_at": row.observed_at.isoformat(),
            "owner_confirmed": row.owner_confirmed,
            "superseded": row.superseded,
            "stale": row.stale,
        }
        for row in rows
    ]


@router.get("/company/questions")
async def get_questions(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = await generate_questions(db, auth.tenant.id)
    await db.commit()
    return rows


@router.post("/company/answers")
async def post_answer(
    payload: AnswerInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await answer_question(
            db,
            auth.tenant.id,
            payload.question_id,
            payload.answer,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    await db.commit()
    return result


@router.patch("/company/profile")
async def patch_profile(
    payload: ProfilePatch,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        for key, value in payload.fields.items():
            await observe_evidence(
                db,
                auth.tenant.id,
                key,
                value,
                "owner",
                confidence=1,
                owner_confirmed=True,
                source_reference=f"owner:{auth.user.id}",
            )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    result = await synthesize_profile(db, auth.tenant.id)
    await generate_questions(db, auth.tenant.id)
    await db.commit()
    return result


@router.get("/company/attention")
async def company_attention(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await attention_items(db, auth.tenant.id)


@router.get("/home/command-center")
async def home_command_center(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    state = await get_company_state(auth.tenant.id, db)
    quote_value = await db.scalar(
        select(func.coalesce(func.sum(Quote.total), 0)).where(
            Quote.tenant_id == auth.tenant.id,
            Quote.status.in_(["draft", "sent", "pending"]),
        )
    )
    return {
        "summary": {
            **state.metrics,
            "open_opportunities": sum(
                value
                for stage, value in state.operations["leads_by_stage"].items()
                if stage not in {"won", "lost"}
            ),
            "open_tasks": len(state.operations["open_tasks"]),
            "outstanding_quote_value": float(quote_value or 0),
        },
        "attention": state.attention,
        "recent_actions": state.operations["recent_actions"][:8],
    }


@router.get("/company/events")
async def company_events(
    event_type: str | None = None,
    correlation_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    events = await query_events(
        db,
        auth.tenant.id,
        event_type=event_type,
        correlation_id=correlation_id,
        since=since,
        until=until,
        limit=limit,
    )
    return [
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "event_type": row.event_type,
            "occurred_at": row.occurred_at.isoformat(),
            "actor_type": row.actor_type,
            "actor_id": row.actor_id,
            "source": row.source,
            "payload": row.payload,
            "correlation_id": row.correlation_id,
            "causation_id": row.causation_id,
            "metadata": row.metadata,
        }
        for row in events
    ]


@router.post("/company/plan")
async def create_plan(
    payload: PlanInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    del db
    return await get_agent_service().run(
        AgentInput(
            tenant_id=auth.tenant.id,
            principal_id=f"web-user:{auth.user.id}",
            actor_name=auth.user.display_name,
            channel="web",
            text=payload.objective,
            metadata={
                "user_id": auth.user.id,
                "role": auth.role,
                "entrypoint": "company_plan",
            },
        )
    )


@router.get("/actions")
async def actions(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(BusinessActionRecord)
            .where(BusinessActionRecord.tenant_id == auth.tenant.id)
            .order_by(BusinessActionRecord.created_at.desc())
        )
    ).all()
    return [action_payload(row) for row in rows]


async def _decide(action_id, approve, auth, db):
    service = ActionService(
        db,
        default_registry(),
        authority=set(ROLE_AUTHORITY.get(auth.role, set())),
        actor_id=auth.user.id,
    )
    try:
        row = await (
            service.approve(auth.tenant.id, action_id)
            if approve
            else service.reject(auth.tenant.id, action_id)
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, PermissionError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await db.commit()
    return action_payload(row)


@router.post("/actions/{action_id}/approve")
async def approve_action(
    action_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await _decide(action_id, True, auth, db)


@router.post("/actions/{action_id}/reject")
async def reject_action(
    action_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await _decide(action_id, False, auth, db)
