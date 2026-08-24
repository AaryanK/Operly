"""Durable Postgres-backed orchestration for generated Solutions.

The API only queues work. A trusted worker claims one persisted SolutionJob at a
time with a database lease, heartbeats that lease while planning/coding/building,
and lets another worker reclaim the same job after a crash. Generated source is
still executed only by the isolated runner; this worker is control-plane code.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import and_, desc, or_, select

from packages.coding_harness.execution_loop import build_with_repair
from packages.custom_software.compiler_planning import PLANNING_ENGINE_VERSION as PLANNING_ENGINE
from packages.custom_software.live_planning import PlanningBlocked, PlanningMode, PlannerUnavailable
from packages.custom_software.model_planning_client import planning_mode
from packages.custom_software.plan_service import (
    _persist_first_version,
    _run_live_plan,
    _store_clarification,
    approve,
    plan_version,
)
from packages.custom_software.planner import build_software_plan
from packages.custom_software.planning_orchestrator import PlanningNeedsUserInput
from packages.custom_software.runner_adapters import ExternalRunnerAdapter
from packages.custom_software.schema import SoftwarePlan
from packages.custom_software.service import plan_artifact_graph, slugify
from packages.database.custom_software_models import GeneratedProject, SoftwarePlanRecord, SoftwarePlanVersion
from packages.database.db import SessionFactory, init_db
from packages.database.product_models import SolutionJob, SolutionRecord
from packages.solutions.service import LifecycleStatus, RuntimeType, SolutionType

GENERATED_JOB_TYPE = "generated_generation"


def worker_enabled() -> bool:
    return os.getenv("OPERLY_SOLUTION_WORKER_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _lease_seconds() -> int:
    try:
        return max(30, min(int(os.getenv("OPERLY_SOLUTION_WORKER_LEASE_SECONDS", "120")), 900))
    except ValueError:
        return 120


def _heartbeat_seconds() -> int:
    return max(10, min(_lease_seconds() // 3, 60))


def _poll_seconds() -> float:
    try:
        return max(0.25, min(float(os.getenv("OPERLY_SOLUTION_WORKER_POLL_SECONDS", "2")), 30.0))
    except ValueError:
        return 2.0


def worker_identity() -> str:
    configured = os.getenv("OPERLY_SOLUTION_WORKER_ID", "").strip()
    return configured[:160] if configured else f"{socket.gethostname()}:{uuid4().hex[:12]}"[:160]


def _context(row: SolutionRecord) -> dict[str, Any]:
    try:
        value = json.loads(row.context_json or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _evidence(job: SolutionJob) -> dict[str, Any]:
    try:
        value = json.loads(job.evidence_json or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _logs(job: SolutionJob) -> list[dict[str, Any]]:
    try:
        value = json.loads(job.log_json or "[]")
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _append_log(job: SolutionJob, stage: str, status: str, detail: str | None = None) -> None:
    rows = _logs(job)
    item: dict[str, Any] = {
        "at": datetime.utcnow().isoformat() + "Z",
        "stage": stage,
        "status": status,
    }
    if detail:
        item["detail"] = " ".join(str(detail).split())[:1000]
    rows.append(item)
    job.log_json = json.dumps(rows[-200:], ensure_ascii=False)


def _planning_prompt(name: str, objective: str, context: dict[str, Any]) -> str:
    payload = {
        "name": name,
        "objective": objective,
        "solutionManifest": context.get("solutionManifest", {}),
        "implementationResolution": context.get("implementationResolution", {}),
        "constraints": [
            "Implement every mandatory behavior as executable software, not a mock or brochure.",
            "Use Operly capability bindings for trusted data, identity, secrets, permissions and external services.",
            "Generate acceptance tests for critical state transitions and user interactions.",
            "The first preview must remain private; creation does not authorize publishing or external side effects.",
        ],
    }
    return (
        f"Build the Solution named {name!r}.\n\nOwner objective:\n{objective}\n\n"
        "OPERLY SOLUTION CONTRACT:\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )[:20000]


def _planning_input_digest(name: str, objective: str, context: dict[str, Any]) -> str:
    """Fingerprint only inputs that are allowed to make an approved plan reusable."""
    payload = {
        "planningEngine": PLANNING_ENGINE,
        "name": " ".join(str(name or "").split()),
        "objective": " ".join(str(objective or "").split()),
        "solutionManifest": context.get("solutionManifest", {}),
        "implementationResolution": context.get("implementationResolution", {}),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _reusable_approved_plan(
    db,
    row: SolutionRecord,
    planning_input_digest: str,
) -> SoftwarePlanRecord | None:
    """Return a prior approved plan only when its semantic inputs still match.

    Retries should resume from completed work instead of paying the planner again.
    Older jobs that predate checkpoint fingerprints are intentionally not reused;
    the first run on a new planning engine establishes a fresh trustworthy checkpoint.
    """
    prior_jobs = (
        await db.scalars(
            select(SolutionJob)
            .where(
                SolutionJob.tenant_id == row.tenant_id,
                SolutionJob.solution_id == row.id,
                SolutionJob.job_type.in_(("initial_generation", GENERATED_JOB_TYPE)),
                SolutionJob.plan_id.is_not(None),
            )
            .order_by(desc(SolutionJob.attempt), desc(SolutionJob.created_at))
            .limit(20)
        )
    ).all()
    for prior in prior_jobs:
        evidence = _evidence(prior)
        if evidence.get("planningEngine") != PLANNING_ENGINE:
            continue
        if evidence.get("planningInputDigest") != planning_input_digest:
            continue
        plan = await db.get(SoftwarePlanRecord, prior.plan_id)
        if (
            plan is not None
            and plan.tenant_id == row.tenant_id
            and plan.approved_version is not None
        ):
            return plan
    return None


async def _next_attempt(db, tenant_id: str, solution_id: str) -> int:
    previous = await db.scalar(
        select(SolutionJob)
        .where(
            SolutionJob.tenant_id == tenant_id,
            SolutionJob.solution_id == solution_id,
            SolutionJob.job_type.in_(("initial_generation", GENERATED_JOB_TYPE)),
        )
        .order_by(desc(SolutionJob.attempt))
        .limit(1)
    )
    return int(previous.attempt) + 1 if previous else 1


async def create_generated_placeholder(db, tenant_id: str, user_id: str, name: str, objective: str) -> GeneratedProject:
    base = slugify(name) or "generated-solution"
    slug = base
    suffix = 2
    while await db.scalar(select(GeneratedProject.id).where(GeneratedProject.slug == slug)):
        slug = f"{base}-{suffix}"
        suffix += 1
    project = GeneratedProject(
        tenant_id=tenant_id,
        slug=slug,
        name=name[:200],
        vertical="custom",
        prompt=objective,
        brand_json="{}",
        artifact_graph_json="{}",
        created_by=user_id,
        architecture_pack="custom",
    )
    db.add(project)
    await db.flush()
    return project


async def queue_generated_generation(
    db,
    *,
    row: SolutionRecord,
    user_id: str,
) -> tuple[SolutionRecord, SolutionJob]:
    active = await db.scalar(
        select(SolutionJob)
        .where(
            SolutionJob.tenant_id == row.tenant_id,
            SolutionJob.solution_id == row.id,
            SolutionJob.job_type == GENERATED_JOB_TYPE,
            SolutionJob.status.in_(("queued", "running")),
        )
        .order_by(desc(SolutionJob.attempt))
        .limit(1)
    )
    if active:
        return row, active

    attempt = await _next_attempt(db, row.tenant_id, row.id)
    context = _context(row)
    owner = context.get("ownerIntent") if isinstance(context.get("ownerIntent"), dict) else {}
    objective = " ".join(str(owner.get("objective") or row.description or "").split()).strip()[:8000]
    planning_input_digest = _planning_input_digest(row.name, objective, context)
    reusable_plan = await _reusable_approved_plan(db, row, planning_input_digest)
    source_reference = (
        f"software-plan:{reusable_plan.id}:{reusable_plan.approved_version}"
        if reusable_plan is not None
        else f"owner-intent:{row.id}"
    )
    evidence: dict[str, Any] = {
        "objective": objective,
        "createdBy": user_id,
        "implementationResolution": context.get("implementationResolution", {}),
        "planningEngine": PLANNING_ENGINE,
        "planningInputDigest": planning_input_digest,
    }
    if reusable_plan is not None:
        evidence.update(
            {
                "reusedSoftwarePlanId": reusable_plan.id,
                "reusedSoftwarePlanVersion": reusable_plan.approved_version,
            }
        )
    job = SolutionJob(
        tenant_id=row.tenant_id,
        solution_id=row.id,
        source_version_reference=source_reference,
        job_type=GENERATED_JOB_TYPE,
        status="queued",
        attempt=attempt,
        created_by=user_id,
        plan_id=reusable_plan.id if reusable_plan is not None else None,
        log_json="[]",
        evidence_json=json.dumps(evidence, ensure_ascii=False),
        idempotency_key=f"solution:{row.id}:generated-build:{attempt}",
    )
    _append_log(job, "queue", "queued", "Generated Solution queued for durable worker execution")
    if reusable_plan is not None:
        _append_log(
            job,
            "planning",
            "reused",
            f"Reusing approved SoftwarePlan v{reusable_plan.approved_version}; retry resumes at source generation",
        )
    db.add(job)
    await db.flush()

    initial: dict[str, Any] = {
        "status": "queued",
        "stage": "source_generation" if reusable_plan is not None else "planning",
        "jobId": job.id,
        "attempt": attempt,
    }
    if reusable_plan is not None:
        initial["softwarePlanId"] = reusable_plan.id
        initial["softwarePlanVersion"] = reusable_plan.approved_version
        initial["resumedFromCheckpoint"] = "planning"
    context["initialGeneration"] = initial
    row.lifecycle_status = LifecycleStatus.BUILDING
    row.current_version_reference = None
    row.preview_state = "unavailable"
    row.preview_url = None
    row.context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
    await db.flush()
    return row, job


async def _create_plan_record(db, job: SolutionJob, row: SolutionRecord, user_id: str) -> SoftwarePlanRecord:
    context = _context(row)
    owner = context.get("ownerIntent") if isinstance(context.get("ownerIntent"), dict) else {}
    objective = " ".join(str(owner.get("objective") or row.description or "").split()).strip()[:8000]
    plan = SoftwarePlanRecord(
        tenant_id=row.tenant_id,
        prompt=_planning_prompt(row.name, objective, context),
        created_by=user_id,
        status="planning",
    )
    db.add(plan)
    await db.flush()
    job.plan_id = plan.id
    job.source_version_reference = f"software-plan:{plan.id}:pending"
    evidence = _evidence(job)
    evidence["softwarePlanId"] = plan.id
    job.evidence_json = json.dumps(evidence, ensure_ascii=False)
    _append_log(job, "planning", "running", f"SoftwarePlan {plan.id} persisted before model planning")
    context["initialGeneration"] = {
        "status": "running",
        "stage": "planning",
        "jobId": job.id,
        "attempt": job.attempt,
        "softwarePlanId": plan.id,
    }
    row.context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
    await db.commit()
    await db.refresh(job)
    await db.refresh(plan)
    return plan


def _deterministic_plan(prompt: str) -> SoftwarePlan:
    planned = build_software_plan(prompt)
    data = planned.model_dump()
    data["planningMode"] = "deterministic_test"
    data["planningMetrics"]["planningMode"] = "deterministic_test"
    for item in data["requirementLedger"]:
        item["planningMode"] = "deterministic_test"
    for item in data["planTree"]:
        item["planningMode"] = "deterministic_test"
    return SoftwarePlan.model_validate(data)


async def _ensure_plan(db, job: SolutionJob, row: SolutionRecord, user_id: str):
    plan_row = await db.get(SoftwarePlanRecord, job.plan_id) if job.plan_id else None
    if plan_row is None:
        plan_row = await _create_plan_record(db, job, row, user_id)

    if plan_row.approved_version:
        _, plan = await plan_version(db, plan_row, plan_row.approved_version)
        return plan_row, plan

    existing_version = await db.scalar(
        select(SoftwarePlanVersion).where(
            SoftwarePlanVersion.plan_id == plan_row.id,
            SoftwarePlanVersion.tenant_id == plan_row.tenant_id,
            SoftwarePlanVersion.version == plan_row.current_version,
        )
    )
    if existing_version:
        plan = SoftwarePlan.model_validate_json(existing_version.plan_json)
    else:
        mode = planning_mode()
        if mode == PlanningMode.UNAVAILABLE:
            raise PlannerUnavailable("planner_unavailable")
        if mode == PlanningMode.LIVE_LLM:
            try:
                plan = await _run_live_plan(db, plan_row, plan_row.tenant_id, plan_row.prompt)
            except PlanningNeedsUserInput as error:
                await _store_clarification(db, plan_row, error.questions)
                error.plan_id = plan_row.id
                raise
            except ValidationError as error:
                plan_row.status = "planning_blocked"
                await db.commit()
                details = "; ".join(
                    f"{'.'.join(str(part) for part in item.get('loc', []))}: {item.get('msg', 'invalid')}"
                    for item in error.errors()[:8]
                )
                raise PlanningBlocked(f"live plan projection failed schema validation: {details}") from error
            except Exception:
                plan_row.status = "planning_blocked"
                await db.commit()
                raise
        else:
            plan = _deterministic_plan(plan_row.prompt)
        await _persist_first_version(db, plan_row, user_id, plan)

    if not plan_row.approved_version:
        await approve(db, plan_row, plan_row.current_version)
    await db.refresh(plan_row)
    job.plan_id = plan_row.id
    job.source_version_reference = f"software-plan:{plan_row.id}:{plan_row.approved_version}"
    evidence = _evidence(job)
    evidence["softwarePlanId"] = plan_row.id
    evidence["softwarePlanVersion"] = plan_row.approved_version
    job.evidence_json = json.dumps(evidence, ensure_ascii=False)
    _append_log(job, "planning", "succeeded", f"SoftwarePlan v{plan_row.approved_version} validated and approved")
    await db.commit()
    return plan_row, plan


async def _bind_project(db, project: GeneratedProject, plan_row: SoftwarePlanRecord, plan: SoftwarePlan) -> None:
    project.plan_id = plan_row.id
    project.approved_plan_version = plan_row.approved_version
    project.architecture_pack = "custom"
    project.vertical = "custom"
    design = plan.design.model_dump() if getattr(plan, "design", None) else {}
    design.update({"name": project.name, "vertical": "custom"})
    project.brand_json = json.dumps(design, ensure_ascii=False)
    project.artifact_graph_json = json.dumps(
        plan_artifact_graph(
            plan.model_dump(),
            project.id,
            int(project.version or 1),
            int(plan_row.approved_version or plan_row.current_version),
        ),
        ensure_ascii=False,
    )
    await db.flush()


async def _mark_failed(db, job: SolutionJob, row: SolutionRecord, stage: str, error: Exception) -> None:
    safe_error = " ".join(str(error).split())[:1000] or type(error).__name__
    _append_log(job, stage, "failed", safe_error)
    context = _context(row)
    initial = {
        "status": "retryable",
        "stage": stage,
        "error": safe_error,
        "jobId": job.id,
        "attempt": job.attempt,
    }
    if job.plan_id:
        initial["softwarePlanId"] = job.plan_id
    context["initialGeneration"] = initial
    row.lifecycle_status = LifecycleStatus.FAILED
    row.current_version_reference = None
    row.preview_state = "unavailable"
    row.preview_url = None
    row.context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
    job.status = "failed"
    job.ended_at = datetime.utcnow()
    job.failure_classification = type(error).__name__[:80]
    job.locked_by = None
    job.lease_expires_at = None
    job.heartbeat_at = None
    evidence = _evidence(job)
    evidence["failedStage"] = stage
    evidence["error"] = safe_error
    job.evidence_json = json.dumps(evidence, ensure_ascii=False)
    await db.commit()


async def process_generation_job(db, job: SolutionJob) -> None:
    row = await db.get(SolutionRecord, job.solution_id)
    if row is None or row.tenant_id != job.tenant_id:
        raise LookupError("Generated Solution job lost its Solution record")
    if row.runtime_type != RuntimeType.GENERATED_PROJECT:
        raise ValueError("Generated worker only accepts generated_project Solutions")
    project = await db.get(GeneratedProject, row.runtime_reference)
    if project is None or project.tenant_id != row.tenant_id:
        raise LookupError("Generated Solution runtime is missing")
    user_id = job.created_by or str(_evidence(job).get("createdBy") or "")
    if not user_id:
        raise ValueError("Generated Solution job is missing its creating principal")

    stage = "planning"
    try:
        plan_row, plan = await _ensure_plan(db, job, row, user_id)
        await _bind_project(db, project, plan_row, plan)
        context = _context(row)
        context["softwarePlan"] = {
            "id": plan_row.id,
            "version": plan_row.approved_version,
            "status": plan_row.status,
        }
        context["initialGeneration"] = {
            "status": "running",
            "stage": "source_generation",
            "jobId": job.id,
            "attempt": job.attempt,
            "softwarePlanId": plan_row.id,
            "softwarePlanVersion": plan_row.approved_version,
        }
        row.context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
        job.status = "running"
        _append_log(job, "source_generation", "running", "Generating executable source from the approved requirement ledger")
        await db.commit()

        stage = "source_generation"
        build, source, repairs = await build_with_repair(
            db,
            row.tenant_id,
            user_id,
            plan_row,
            plan,
            job.idempotency_key,
            adapter=ExternalRunnerAdapter(),
        )
        job.source_version_reference = str(source.source_version)
        if build.state != "preview_ready":
            classification = build.failure_classification or build.state or "generated_build_failed"
            raise RuntimeError(f"Generated build did not reach preview_ready: {classification}")

        stage = "preview_readiness"
        _append_log(job, "build", "succeeded", f"Isolated build {build.id} completed")
        _append_log(job, "acceptance_test", "succeeded", "Build, tests, health and acceptance checks passed")
        _append_log(job, stage, "succeeded", "Verified isolated preview is active")
        context = _context(row)
        context["initialGeneration"] = {
            "status": "applied",
            "stage": stage,
            "jobId": job.id,
            "attempt": job.attempt,
            "softwarePlanId": plan_row.id,
            "softwarePlanVersion": plan_row.approved_version,
            "sourceBundleId": source.id,
            "sourceVersion": source.source_version,
            "buildId": build.id,
            "repairCount": len(repairs),
        }
        row.lifecycle_status = LifecycleStatus.PREVIEW_READY
        row.current_version_reference = str(source.source_version)
        row.preview_state = "ready"
        row.preview_url = "/api/solutions/{solution_id}/preview"
        row.context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
        job.status = "succeeded"
        job.ended_at = datetime.utcnow()
        job.failure_classification = None
        job.locked_by = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        evidence = _evidence(job)
        evidence.update(
            {
                "softwarePlanId": plan_row.id,
                "softwarePlanVersion": plan_row.approved_version,
                "sourceBundleId": source.id,
                "sourceVersion": source.source_version,
                "buildId": build.id,
                "buildState": build.state,
                "repairs": repairs,
            }
        )
        job.evidence_json = json.dumps(evidence, ensure_ascii=False)
        await db.commit()
    except Exception as error:
        await _mark_failed(db, job, row, stage, error)


async def claim_next_generation_job(worker_id: str) -> str | None:
    now = datetime.utcnow()
    lease_until = now + timedelta(seconds=_lease_seconds())
    async with SessionFactory() as db:
        statement = (
            select(SolutionJob)
            .where(
                SolutionJob.job_type == GENERATED_JOB_TYPE,
                SolutionJob.cancellation_requested.is_(False),
                or_(
                    SolutionJob.status == "queued",
                    and_(
                        SolutionJob.status == "running",
                        SolutionJob.lease_expires_at.is_not(None),
                        SolutionJob.lease_expires_at < now,
                    ),
                ),
            )
            .order_by(SolutionJob.queued_at, SolutionJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = await db.scalar(statement)
        if job is None:
            return None
        reclaimed = job.status == "running"
        job.status = "running"
        job.started_at = job.started_at or now
        job.locked_by = worker_id
        job.heartbeat_at = now
        job.lease_expires_at = lease_until
        if reclaimed:
            _append_log(job, "worker_lease", "reclaimed", "Expired worker lease reclaimed after interruption")
        else:
            _append_log(job, "worker_lease", "claimed", f"Claimed by {worker_id}")
        await db.commit()
        return job.id


async def _heartbeat(job_id: str, worker_id: str, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=_heartbeat_seconds())
            return
        except asyncio.TimeoutError:
            pass
        async with SessionFactory() as db:
            job = await db.get(SolutionJob, job_id)
            if job is None or job.status != "running" or job.locked_by != worker_id:
                return
            now = datetime.utcnow()
            job.heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=_lease_seconds())
            await db.commit()


async def work_once(worker_id: str) -> bool:
    job_id = await claim_next_generation_job(worker_id)
    if not job_id:
        return False
    stop = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat(job_id, worker_id, stop))
    try:
        async with SessionFactory() as db:
            job = await db.get(SolutionJob, job_id)
            if job is None or job.locked_by != worker_id:
                return True
            await process_generation_job(db, job)
    finally:
        stop.set()
        await heartbeat
    return True


async def run_forever() -> None:
    # This lets the trusted Railway worker service be provisioned before the
    # isolated runner rollout. Disabled workers intentionally do not touch the DB
    # or consume queued attempts; enabling the variable requires a redeploy.
    if not worker_enabled():
        while True:
            await asyncio.sleep(3600)
    await init_db()
    worker_id = worker_identity()
    while True:
        did_work = await work_once(worker_id)
        if not did_work:
            await asyncio.sleep(_poll_seconds())


if __name__ == "__main__":
    asyncio.run(run_forever())
