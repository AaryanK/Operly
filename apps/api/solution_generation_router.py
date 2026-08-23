import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from apps.api.solution_generation_http import generation_failure_response
from packages.database.product_models import SolutionJob
from packages.solutions.composer import retry_solution_initial_generation
from packages.solutions.service import LifecycleStatus, SolutionService, solution_json


router = APIRouter(prefix="/api/solutions", tags=["solutions"])
service = SolutionService()


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

    if row.lifecycle_status == LifecycleStatus.FAILED:
        await db.commit()
        return generation_failure_response(row)

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
                "callsTruncated": bool((trace or {}).get("callsTruncated")),
                "attemptsTruncated": bool((trace or {}).get("attemptsTruncated")),
            }
        )
    return {"solutionId": solution_id, "attempts": items}
