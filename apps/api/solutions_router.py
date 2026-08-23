import os
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.actions.service import ActionService
from packages.business.service import BusinessService
from packages.capabilities.agent_harness import ROLE_AUTHORITY
from packages.capabilities.defaults import default_registry
from packages.company.events import append_event
from packages.database.product_models import (
    SolutionDeployment,
    SolutionDomain,
    SolutionImprovementProposal,
    SolutionJob,
)
from packages.solutions import SolutionService, SolutionType
from packages.solutions.composer import create_solution_from_intent
from packages.solutions.operations import PresenceOperationsService, proposal_json
from packages.solutions.production import ProductionService, job_json
from packages.solutions.service import solution_json

router = APIRouter(prefix="/api/solutions", tags=["solutions"])
service = SolutionService()


class CreateSolutionInput(BaseModel):
    solution_type: str = Field(default=SolutionType.DIGITAL_PRESENCE)
    name: str | None = Field(default=None, max_length=200)


class ComposeSolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=2, max_length=8000)


class DomainInput(BaseModel):
    domain: str = Field(
        min_length=4,
        max_length=253,
        pattern=r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[A-Za-z]{2,63}$",
    )


@router.get("")
async def list_solutions(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = await service.list(db, auth.tenant.id)
    await db.commit()
    return [solution_json(row) for row in rows]


@router.post("")
async def create_solution(
    payload: CreateSolutionInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Legacy Digital Presence endpoint retained for existing clients."""
    if payload.solution_type != SolutionType.DIGITAL_PRESENCE:
        raise HTTPException(
            status_code=422,
            detail="Use /api/solutions/compose for intent-driven Solution creation",
        )
    try:
        row = await service.create_presence(
            db,
            auth.tenant.id,
            auth.user.id,
            payload.name,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await db.commit()
    return solution_json(row)


@router.post("/compose", status_code=201)
async def compose_solution(
    payload: ComposeSolutionInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Classify owner intent before selecting/creating the Solution runtime."""
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can create Solutions")
    try:
        row, decision = await create_solution_from_intent(
            db,
            tenant_id=auth.tenant.id,
            user_id=auth.user.id,
            name=payload.name,
            objective=payload.objective,
            service=service,
        )
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    await db.commit()
    return {"solution": solution_json(row), "classification": decision.as_dict()}


@router.get("/{solution_id}")
async def get_solution(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        return solution_json(await service.get(db, auth.tenant.id, solution_id))
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/{solution_id}/preview")
async def preview_solution(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        row, runtime = await service.resolve(db, auth.tenant.id, solution_id)
        url = await service.preview_target(db, auth.tenant.id, row, runtime)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return RedirectResponse(url, status_code=307)


@router.post("/{solution_id}/approve")
async def approve_solution(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can publish a business presence")
    try:
        job, row = await service.approve(
            db,
            auth.tenant.id,
            solution_id,
            auth.user.id,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await db.commit()
    return {"solution": solution_json(row), "job": job_json(job)}


@router.get("/{solution_id}/versions")
async def solution_versions(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await service.versions(db, auth.tenant.id, solution_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/{solution_id}/jobs")
async def solution_jobs(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        await service.get(db, auth.tenant.id, solution_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    rows = (
        await db.scalars(
            select(SolutionJob)
            .where(
                SolutionJob.tenant_id == auth.tenant.id,
                SolutionJob.solution_id == solution_id,
            )
            .order_by(desc(SolutionJob.created_at))
            .limit(25)
        )
    ).all()
    return [job_json(row) for row in rows]


@router.post("/{solution_id}/observe")
async def observe_solution(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await PresenceOperationsService(service).observe(
            db,
            auth.tenant.id,
            solution_id,
            force=True,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    await db.commit()
    return result


@router.get("/{solution_id}/improvements")
async def solution_improvements(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        await service.get(db, auth.tenant.id, solution_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    rows = (
        await db.scalars(
            select(SolutionImprovementProposal)
            .where(
                SolutionImprovementProposal.tenant_id == auth.tenant.id,
                SolutionImprovementProposal.solution_id == solution_id,
            )
            .order_by(desc(SolutionImprovementProposal.created_at))
        )
    ).all()
    return [proposal_json(row) for row in rows]


@router.post("/{solution_id}/improvements/{proposal_id}/review")
async def review_improvement(
    solution_id: str,
    proposal_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can approve a website change")
    proposal = await db.scalar(
        select(SolutionImprovementProposal).where(
            SolutionImprovementProposal.id == proposal_id,
            SolutionImprovementProposal.solution_id == solution_id,
            SolutionImprovementProposal.tenant_id == auth.tenant.id,
        )
    )
    if not proposal:
        raise HTTPException(status_code=404, detail="Improvement proposal not found")

    action = await ActionService(
        db,
        default_registry(),
        authority=set(ROLE_AUTHORITY["owner"]),
        actor_id=auth.user.id,
    ).propose(
        tenant_id=auth.tenant.id,
        objective=proposal.expected_outcome,
        capability="solution.apply_improvement",
        arguments={"proposal_id": proposal.id},
        rationale=proposal.issue,
        expected_outcome=proposal.expected_outcome,
        risk_level=proposal.risk,
        idempotency_key=f"proposal:{proposal.id}:approval",
    )
    proposal.action_id = action.id
    await db.commit()
    return {
        "proposal": proposal_json(proposal),
        "action_id": action.id,
        "approval_id": action.approval_id,
        "status": action.status,
    }


@router.post("/{solution_id}/rollback")
async def rollback_solution(
    solution_id: str,
    payload: dict | None = None,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can roll back a business presence")
    try:
        job, row = await ProductionService(service).rollback(
            db,
            auth.tenant.id,
            solution_id,
            auth.user.id,
            (payload or {}).get("idempotency_key"),
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await db.commit()
    return {"solution": solution_json(row), "job": job_json(job)}


@router.get("/{solution_id}/domains")
async def solution_domains(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        await service.get(db, auth.tenant.id, solution_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    rows = (
        await db.scalars(
            select(SolutionDomain).where(
                SolutionDomain.tenant_id == auth.tenant.id,
                SolutionDomain.solution_id == solution_id,
            )
        )
    ).all()
    import json

    return [
        {
            "id": row.id,
            "domain": row.requested_domain,
            "verification_state": row.verification_state,
            "dns_requirements": json.loads(row.dns_requirements_json),
            "ssl_state": row.ssl_state,
        }
        for row in rows
    ]


@router.post("/{solution_id}/domains")
async def request_domain(
    solution_id: str,
    payload: DomainInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can request a custom domain")
    try:
        await service.get(db, auth.tenant.id, solution_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    domain = payload.domain.lower()
    existing = await db.scalar(
        select(SolutionDomain).where(
            SolutionDomain.tenant_id == auth.tenant.id,
            SolutionDomain.requested_domain == domain,
        )
    )
    if existing:
        import json

        return {
            "id": existing.id,
            "domain": domain,
            "verification_state": existing.verification_state,
            "dns_requirements": json.loads(existing.dns_requirements_json),
            "ssl_state": existing.ssl_state,
        }

    import json

    requirements = {
        "type": "CNAME",
        "name": domain,
        "value": "domains.operly.example",
        "automated_change": False,
    }
    row = SolutionDomain(
        tenant_id=auth.tenant.id,
        solution_id=solution_id,
        requested_domain=domain,
        verification_state="pending",
        dns_requirements_json=json.dumps(requirements),
        ssl_state="pending",
    )
    db.add(row)
    await db.commit()
    return {
        "id": row.id,
        "domain": domain,
        "verification_state": row.verification_state,
        "dns_requirements": requirements,
        "ssl_state": row.ssl_state,
    }


public_router = APIRouter(tags=["published presence"])


@public_router.post(
    "/api/public/presence/{solution_id}/forms/{form_key}",
    include_in_schema=False,
)
async def published_presence_form(
    solution_id: str,
    form_key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if int(request.headers.get("content-length", "0") or 0) > 50_000:
        raise HTTPException(status_code=413, detail="Submission too large")

    deployment = await db.scalar(
        select(SolutionDeployment).where(
            SolutionDeployment.solution_id == solution_id,
            SolutionDeployment.status == "active",
            SolutionDeployment.health_state == "healthy",
        )
    )
    if not deployment or form_key != "contact":
        raise HTTPException(status_code=404, detail="Form not found")

    values = {
        key: value[-1][:4000]
        for key, value in parse_qs(
            (await request.body()).decode("utf-8", "replace"),
            keep_blank_values=True,
            max_num_fields=20,
        ).items()
    }
    if values.pop("website", ""):
        raise HTTPException(status_code=400, detail="Submission rejected")

    name = values.get("name", "").strip()
    email = values.get("email", "").strip()
    message = values.get("message", "").strip()
    if not name or not email or not message:
        raise HTTPException(
            status_code=422,
            detail="Name, email, and message are required",
        )

    contact = await BusinessService.create_contact(
        db,
        deployment.tenant_id,
        name=name[:200],
        email=email[:320],
        source="published_presence",
        actor="published_presence",
    )
    lead = await BusinessService.create_lead(
        db,
        deployment.tenant_id,
        title=f"Website inquiry from {name[:120]}",
        contact_id=contact.id,
        stage="new",
        next_action=message[:2000],
        actor="published_presence",
    )

    event = await append_event(
        db,
        tenant_id=deployment.tenant_id,
        event_type="form.submitted",
        payload={
            "solution_id": solution_id,
            "deployment_id": deployment.id,
            "form_key": form_key,
            "contact_id": contact.id,
            "lead_id": lead.id,
            "fields": ["name", "email", "message"],
        },
        source="published_presence",
    )
    await append_event(
        db,
        tenant_id=deployment.tenant_id,
        event_type="lead.created",
        payload={
            "lead_id": lead.id,
            "contact_id": contact.id,
            "solution_id": solution_id,
            "source_event_id": event.id,
        },
        correlation_id=event.correlation_id,
        causation_id=event.id,
        source="published_presence",
    )
    await db.commit()
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Thank you</title><p>Thank you. Your submission was received.</p>",
        headers={"Content-Security-Policy": "default-src 'none'"},
    )


@public_router.get("/presence/{solution_id}", include_in_schema=False)
async def published_presence(
    solution_id: str,
    db: AsyncSession = Depends(get_db),
):
    deployment = await db.scalar(
        select(SolutionDeployment)
        .where(
            SolutionDeployment.solution_id == solution_id,
            SolutionDeployment.status == "active",
            SolutionDeployment.health_state == "healthy",
        )
        .order_by(desc(SolutionDeployment.deployed_at))
    )
    if not deployment:
        raise HTTPException(status_code=404, detail="Published presence not found")

    root_value = os.getenv("OPERLY_DEPLOYMENT_ROOT", "")
    if not root_value:
        raise HTTPException(
            status_code=503,
            detail="Published presence storage is unavailable",
        )
    root = Path(root_value).resolve()
    artifact = Path(deployment.artifact_reference).resolve()
    if root not in artifact.parents or not artifact.is_file():
        raise HTTPException(
            status_code=503,
            detail="Published presence artifact is unavailable",
        )
    return FileResponse(
        artifact,
        media_type="text/html",
        headers={
            "Content-Security-Policy": "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=60",
        },
    )
