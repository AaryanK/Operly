import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.product_models import SolutionJob
from packages.solutions.composer import create_solution_from_intent, retry_solution_initial_generation
from packages.solutions.service import LifecycleStatus, SolutionService, solution_json


router = APIRouter(prefix="/api/solutions", tags=["solutions"])
service = SolutionService()


class ComposeSolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=2, max_length=8000)


def retire_legacy_compose_route(canonical_router: APIRouter) -> None:
    """Move `/compose` ownership to this generation-lifecycle router.

    This keeps exactly one POST route while the rest of the long-lived Solution
    API remains in `solutions_router`.
    """
    canonical_router.routes[:] = [
        route
        for route in canonical_router.routes
        if not (
            getattr(route, "path", "") == "/api/solutions/compose"
            and "POST" in (getattr(route, "methods", set()) or set())
        )
    ]


@router.post("/compose", status_code=201)
async def compose_solution(
    payload: ComposeSolutionInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Create a Solution and report initial-generation failure truthfully."""
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

    payload_json = solution_json(row)
    if row.lifecycle_status == LifecycleStatus.FAILED:
        # Persist the failed runtime/job before returning a non-success HTTP state.
        # The browser therefore cannot announce creation as successful, while the
        # exact objective and retryable run remain available for inspection/retry.
        await db.commit()
        generation = payload_json.get("generation") or {}
        stage = generation.get("stage") or "initial_generation"
        reason = generation.get("error") or "Initial application generation failed safely."
        return JSONResponse(
            status_code=502,
            content={
                "detail": {
                    "code": "initial_generation_failed",
                    "message": f"Application generation stopped at {stage}: {reason}",
                    "failedStage": stage,
                    "solution": payload_json,
                    "retryEndpoint": f"/api/solutions/{row.id}/retry-generation",
                    "traceEndpoint": f"/api/solutions/{row.id}/generation-trace",
                }
            },
        )

    await db.commit()
    return {"solution": payload_json, "classification": decision.as_dict()}


@router.post("/{solution_id}/retry-generation")
async def retry_solution_generation(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Retry managed-app initial generation from the stored owner objective."""
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can retry Solution generation")
    try:
        row = await retry_solution_initial_generation(
            db,
            tenant_id=auth.tenant.id,
            user_id=auth.user.id,
            solution_id=solution_id,
            service=service,
        )
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    await db.commit()
    return {"solution": solution_json(row)}


@router.get("/{solution_id}/generation-trace")
async def generation_trace(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Owner-only redacted model-boundary trace for managed-app creation attempts."""
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can inspect generation traces")
    try:
        await service.get(db, auth.tenant.id, solution_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    jobs = list(
        (
            await db.scalars(
                select(SolutionJob)
                .where(
                    SolutionJob.tenant_id == auth.tenant.id,
                    SolutionJob.solution_id == solution_id,
                    SolutionJob.job_type == "initial_generation",
                )
                .order_by(desc(SolutionJob.attempt))
                .limit(20)
            )
        ).all()
    )
    items = []
    for job in jobs:
        try:
            evidence = json.loads(job.evidence_json or "{}")
        except Exception:
            evidence = {}
        try:
            logs = json.loads(job.log_json or "[]")
        except Exception:
            logs = []
        trace = evidence.get("modelTrace") if isinstance(evidence, dict) else None
        items.append(
            {
                "jobId": job.id,
                "attempt": job.attempt,
                "status": job.status,
                "failureClassification": job.failure_classification,
                "startedAt": job.started_at.isoformat() if job.started_at else None,
                "endedAt": job.ended_at.isoformat() if job.ended_at else None,
                "stages": logs if isinstance(logs, list) else [],
                "aiInvoked": bool((trace or {}).get("aiInvoked")),
                "modelCalls": (trace or {}).get("modelCalls", []),
                "modelAttempts": (trace or {}).get("modelAttempts", []),
            }
        )
    return {"solutionId": solution_id, "attempts": items}
