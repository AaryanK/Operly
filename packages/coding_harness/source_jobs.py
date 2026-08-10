"""Background source-generation jobs with database-backed observable state."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from packages.coding_harness.opencode_agent import CodingAgentNeedsUserInput
from packages.coding_harness.source_service import generate_source_for_plan, source_record_json
from packages.custom_software.live_planning import PlannerUnavailable
from packages.custom_software.plan_service import plan_version
from packages.database.custom_software_models import SandboxGenerationJob, SandboxJobEvent, SoftwarePlanRecord
from packages.database.db import SessionFactory


_running_tasks: set[asyncio.Task] = set()


def job_json(row: SandboxGenerationJob) -> dict:
    result = json.loads(row.result_json or "{}")
    return {
        "id": row.id,
        "planId": row.plan_id,
        "approvedVersion": row.approved_plan_version,
        "state": row.state,
        "attempts": row.attempts,
        "result": result,
        "failure": row.failure_message,
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
    }


async def _event(db, row: SandboxGenerationJob, state: str, **details) -> None:
    row.state = state
    row.updated_at = datetime.utcnow()
    db.add(SandboxJobEvent(tenant_id=row.tenant_id, job_id=row.id, state=state, details_json=json.dumps(details)))
    await db.commit()


async def run_source_job(job_id: str) -> None:
    try:
        async with SessionFactory() as db:
            job = await db.get(SandboxGenerationJob, job_id)
            if job is None:
                return
            await _event(db, job, "generating", message="Coding agent is authoring the source workspace")
            plan_row = await db.get(SoftwarePlanRecord, job.plan_id)
            if plan_row is None or plan_row.tenant_id != job.tenant_id:
                raise LookupError("Approved software plan no longer exists")
            _, plan = await plan_version(db, plan_row, job.approved_plan_version)
            async def progress(event: dict) -> None:
                async with SessionFactory() as progress_db:
                    progress_job = await progress_db.get(SandboxGenerationJob, job_id)
                    if progress_job is None or progress_job.state not in {"queued", "generating"}:
                        return
                    current = json.loads(progress_job.result_json or "{}")
                    activity = list(current.get("activity") or [])[-39:]
                    activity.append(event)
                    progress_job.result_json = json.dumps({"current": event, "activity": activity})
                    progress_job.updated_at = datetime.utcnow()
                    progress_db.add(SandboxJobEvent(tenant_id=progress_job.tenant_id, job_id=progress_job.id, state="generating", details_json=json.dumps(event)))
                    await progress_db.commit()

            source, _ = await generate_source_for_plan(db, job.tenant_id, job.created_by, plan_row, plan, progress_callback=progress)
            await db.commit()
            await db.refresh(source)
            job.result_json = json.dumps({"source": source_record_json(source)})
            await _event(db, job, "completed", message="Source workspace is ready", sourceId=source.id)
    except Exception as error:
        async with SessionFactory() as db:
            job = await db.get(SandboxGenerationJob, job_id)
            if job is None:
                return
            message = str(error)[:4000]
            if isinstance(error, CodingAgentNeedsUserInput):
                job.result_json = json.dumps({"question": error.question, "options": error.options})
                message = error.question
            elif isinstance(error, PlannerUnavailable):
                message = "The configured coding model is unavailable"
            job.failure_message = message
            await _event(db, job, "failed", message=message)


def launch_source_job(job_id: str) -> None:
    task = asyncio.create_task(run_source_job(job_id), name=f"coding-source-{job_id}")
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)
