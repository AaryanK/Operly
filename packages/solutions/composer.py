"""Intent-first Solution creation shared by UI and future agent surfaces."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select

from packages.application_builder.schema import BuilderContext, ProposalRequest
from packages.application_builder.service import ApplicationBuilderService
from packages.database.application_builder_models import ApplicationVersion
from packages.database.product_models import SolutionJob
from packages.solutions.model_trace import begin as begin_model_trace
from packages.solutions.model_trace import end as end_model_trace
from packages.solutions.model_trace import snapshot as model_trace_snapshot
from packages.solutions.service import LifecycleStatus, RuntimeType, SolutionService, SolutionType
from packages.studio.service import StudioService


@dataclass(frozen=True, slots=True)
class SolutionIntent:
    solution_type: str
    runtime_type: str
    reason: str
    confidence: str

    def as_dict(self) -> dict[str, str]:
        return {
            "solutionType": self.solution_type,
            "runtimeType": self.runtime_type,
            "reason": self.reason,
            "confidence": self.confidence,
        }


_WEB_TERMS = {
    "website", "site", "landing", "homepage", "portfolio", "brochure", "marketing",
    "public", "presence", "seo", "pages", "webpage",
}
_APP_TERMS = {
    "app", "application", "attendance", "logger", "logging", "track", "tracking",
    "record", "records", "inventory", "dashboard", "crm", "workflow", "portal",
    "database", "checkin", "checkout", "check-in", "check-out", "arrival", "departure",
    "manage", "management", "internal", "system", "tool", "form", "save", "store",
}
_STATE_TERMS = {
    "save", "store", "record", "records", "logging", "track", "tracking", "inventory",
    "attendance", "database", "manage", "management", "checkin", "checkout", "arrival", "departure",
}


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9][a-z0-9-]+", str(value or "").lower()))


def classify_solution_intent(name: str, objective: str) -> SolutionIntent:
    """Choose the supported runtime before creating it.

    Digital Presence is deliberately opt-in through clear public-site intent. Any
    objective that describes records/state/workflows is routed to the managed app
    runtime so requests such as "Student Attendance Logger" cannot be forced into a
    static website merely because creation started from the Solutions page.
    """

    tokens = _tokens(f"{name} {objective}")
    web_score = len(tokens & _WEB_TERMS)
    app_score = len(tokens & _APP_TERMS)
    stateful = bool(tokens & _STATE_TERMS)

    if stateful or app_score > web_score:
        return SolutionIntent(
            SolutionType.BUSINESS_APP,
            RuntimeType.MANAGED_APP,
            "The request describes state, records, workflows, or application behavior.",
            "high" if stateful or app_score >= 2 else "medium",
        )
    if web_score:
        return SolutionIntent(
            SolutionType.DIGITAL_PRESENCE,
            RuntimeType.STUDIO,
            "The request explicitly describes a public website/digital-presence experience.",
            "high" if web_score >= 2 else "medium",
        )
    return SolutionIntent(
        SolutionType.BUSINESS_APP,
        RuntimeType.MANAGED_APP,
        "No explicit public-website requirement was supplied; preserve functional intent in the app runtime.",
        "low",
    )


def _context(row) -> dict[str, Any]:
    try:
        value=json.loads(row.context_json or "{}")
    except Exception:
        return {}
    return value if isinstance(value,dict) else {}


def _log(logs: list[dict[str, Any]], stage: str, status: str, detail: str | None = None) -> None:
    item={"at":datetime.utcnow().isoformat()+"Z","stage":stage,"status":status}
    if detail:item["detail"]=" ".join(str(detail).split())[:1000]
    logs.append(item)


async def _next_generation_attempt(db, tenant_id: str, solution_id: str) -> int:
    previous=await db.scalar(
        select(SolutionJob)
        .where(
            SolutionJob.tenant_id==tenant_id,
            SolutionJob.solution_id==solution_id,
            SolutionJob.job_type=="initial_generation",
        )
        .order_by(desc(SolutionJob.attempt))
        .limit(1)
    )
    return int(previous.attempt)+1 if previous else 1


async def _run_managed_generation(
    db,
    *,
    tenant_id: str,
    user_id: str,
    row,
    app,
    base_version: ApplicationVersion,
):
    context=_context(row)
    owner=context.get("ownerIntent") if isinstance(context.get("ownerIntent"),dict) else {}
    objective=" ".join(str(owner.get("objective") or row.description or "").split()).strip()[:8000]
    if not objective:raise ValueError("The stored Solution creation objective is missing")

    attempt=await _next_generation_attempt(db,tenant_id,row.id)
    logs: list[dict[str, Any]]=[]
    _log(logs,"objective","succeeded","Stored owner objective loaded")
    _log(logs,"runtime_bootstrap","succeeded",f"Managed app bootstrap version {base_version.version_number}")
    job=SolutionJob(
        tenant_id=tenant_id,
        solution_id=row.id,
        source_version_reference=base_version.id,
        job_type="initial_generation",
        status="running",
        attempt=attempt,
        started_at=datetime.utcnow(),
        log_json=json.dumps(logs,ensure_ascii=False),
        evidence_json=json.dumps({"objective":objective,"bootstrapVersionId":base_version.id},ensure_ascii=False),
        idempotency_key=f"solution:{row.id}:initial-generation:{attempt}",
    )
    db.add(job);await db.flush()
    stage="proposal"
    context["initialGeneration"]={
        "status":"running",
        "stage":stage,
        "jobId":job.id,
        "attempt":attempt,
        "bootstrapVersionId":base_version.id,
    }
    row.lifecycle_status=LifecycleStatus.BUILDING
    row.current_version_reference=None
    row.preview_state="unavailable"
    row.preview_url=None
    row.context_json=json.dumps(context,ensure_ascii=False,sort_keys=True)
    await db.flush()

    trace_token=begin_model_trace(job.id)
    try:
        _log(logs,stage,"running","Generating an application change set from the owner objective")
        job.log_json=json.dumps(logs,ensure_ascii=False)
        change=await ApplicationBuilderService.propose(
            db,
            tenant_id,
            user_id,
            "owner",
            ProposalRequest(
                message=objective,
                context=BuilderContext(
                    workspaceId=tenant_id,
                    applicationId=app.id,
                    activeVersionId=base_version.id,
                    userRole="owner",
                    selectionScope="application",
                ),
            ),
        )
        trace=model_trace_snapshot()
        if trace["aiInvoked"]:
            _log(logs,"model_boundary","succeeded",f"Captured {len(trace['modelAttempts'])} provider/model attempt events")
        else:
            _log(logs,"model_boundary","succeeded","AI was not invoked; a bounded deterministic application capability satisfied the objective")
        _log(logs,stage,"succeeded",f"Change set {change.id} validated")
        stage="apply"
        _log(logs,stage,"running","Applying the generated validated manifest")
        job.log_json=json.dumps(logs,ensure_ascii=False)
        version=await ApplicationBuilderService.apply(
            db,
            tenant_id,
            user_id,
            "owner",
            change.id,
        )
        _log(logs,stage,"succeeded",f"Generated version {version.version_number} applied")
        _log(logs,"preview_readiness","succeeded","A non-bootstrap generated version is active")
        context["initialGeneration"]={
            "changeSetId":change.id,
            "versionId":version.id,
            "bootstrapVersionId":base_version.id,
            "jobId":job.id,
            "attempt":attempt,
            "stage":"preview_readiness",
            "status":"applied",
        }
        row.lifecycle_status=LifecycleStatus.PREVIEW_READY
        row.current_version_reference=version.id
        row.preview_state="ready"
        row.preview_url="/api/solutions/{solution_id}/preview"
        row.context_json=json.dumps(context,ensure_ascii=False,sort_keys=True)
        job.status="succeeded"
        job.ended_at=datetime.utcnow()
        job.log_json=json.dumps(logs,ensure_ascii=False)
        job.evidence_json=json.dumps({
            "objective":objective,
            "bootstrapVersionId":base_version.id,
            "changeSetId":change.id,
            "versionId":version.id,
            "modelTrace":trace,
        },ensure_ascii=False)
        job.failure_classification=None
        await db.flush()
        return row
    except Exception as error:
        trace=model_trace_snapshot()
        safe_error=" ".join(str(error).split())[:1000] or type(error).__name__
        if trace["aiInvoked"]:
            _log(logs,"model_boundary","failed" if stage=="proposal" else "succeeded",f"Captured {len(trace['modelAttempts'])} provider/model attempt events")
        else:
            _log(logs,"model_boundary","succeeded","AI was not invoked before the failed prerequisite/stage")
        _log(logs,stage,"failed",safe_error)
        context["initialGeneration"]={
            "status":"retryable",
            "stage":stage,
            "error":safe_error,
            "jobId":job.id,
            "attempt":attempt,
            "bootstrapVersionId":base_version.id,
        }
        row.lifecycle_status=LifecycleStatus.FAILED
        row.current_version_reference=None
        row.preview_state="unavailable"
        row.preview_url=None
        row.context_json=json.dumps(context,ensure_ascii=False,sort_keys=True)
        job.status="failed"
        job.ended_at=datetime.utcnow()
        job.log_json=json.dumps(logs,ensure_ascii=False)
        job.failure_classification=type(error).__name__[:80]
        job.evidence_json=json.dumps({
            "objective":objective,
            "bootstrapVersionId":base_version.id,
            "failedStage":stage,
            "modelTrace":trace,
        },ensure_ascii=False)
        await db.flush()
        return row
    finally:
        end_model_trace(trace_token)


async def retry_solution_initial_generation(
    db,
    *,
    tenant_id: str,
    user_id: str,
    solution_id: str,
    service: SolutionService | None = None,
):
    service=service or SolutionService()
    row,runtime=await service.resolve(db,tenant_id,solution_id)
    if row.runtime_type!=RuntimeType.MANAGED_APP:
        raise ValueError("Only managed Business apps have this generation lifecycle")
    if row.preview_state=="ready" and row.lifecycle_status==LifecycleStatus.PREVIEW_READY:
        raise ValueError("This Solution already has a generated preview-ready version")
    if not runtime.active_version_id:
        raise ValueError("The managed application bootstrap version is missing")
    base_version=await db.get(ApplicationVersion,runtime.active_version_id)
    if not base_version or base_version.application_id!=runtime.id or base_version.tenant_id!=tenant_id:
        raise ValueError("The managed application bootstrap version could not be resolved")
    return await _run_managed_generation(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        row=row,
        app=runtime,
        base_version=base_version,
    )


async def create_solution_from_intent(
    db,
    *,
    tenant_id: str,
    user_id: str,
    name: str,
    objective: str,
    service: SolutionService | None = None,
):
    service = service or SolutionService()
    clean_name = " ".join(str(name or "").split()).strip()[:200]
    clean_objective = " ".join(str(objective or "").split()).strip()[:8000]
    if not clean_name:
        raise ValueError("Solution name is required")
    if not clean_objective:
        raise ValueError("Describe what this Solution should do")

    decision = classify_solution_intent(clean_name, clean_objective)
    context: dict[str, Any] = {
        "ownerIntent": {"name": clean_name, "objective": clean_objective},
        "creationIntent": {
            "name": clean_name,
            "objective": clean_objective,
            "classification": decision.as_dict(),
        },
        "contextAuthority": ["ownerIntent", "solution", "workspaceInherited"],
    }

    if decision.runtime_type == RuntimeType.STUDIO:
        project = await StudioService.create_project(
            db,
            tenant_id,
            user_id,
            clean_name,
            clean_objective,
        )
        context["source_engine"] = "studio_source_agent_v1"
        row = await service._record(
            db,
            tenant_id,
            RuntimeType.STUDIO,
            project.id,
            name=clean_name,
            description=clean_objective,
            solution_type=SolutionType.DIGITAL_PRESENCE,
            lifecycle_status=LifecycleStatus.DRAFT,
            current_version_reference=project.active_draft_version_id,
            preview_state="ready" if project.active_draft_version_id else "unavailable",
            preview_url="/api/solutions/{solution_id}/preview",
            production_state="offline",
            production_url=None,
            visibility="private",
            context_json=json.dumps(context, ensure_ascii=False, sort_keys=True),
        )
        return row, decision

    app, version = await ApplicationBuilderService.create(
        db,
        tenant_id,
        user_id,
        clean_name,
        clean_objective,
    )
    context["initialGeneration"]={
        "status":"pending",
        "stage":"runtime_bootstrap",
        "bootstrapVersionId":version.id,
    }
    row=await service._record(
        db,
        tenant_id,
        RuntimeType.MANAGED_APP,
        app.id,
        name=clean_name,
        description=clean_objective,
        solution_type=SolutionType.BUSINESS_APP,
        lifecycle_status=LifecycleStatus.BUILDING,
        current_version_reference=None,
        preview_state="unavailable",
        preview_url=None,
        production_state="offline",
        production_url=None,
        visibility="private",
        context_json=json.dumps(context,ensure_ascii=False,sort_keys=True),
    )
    await _run_managed_generation(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        row=row,
        app=app,
        base_version=version,
    )
    return row, decision
