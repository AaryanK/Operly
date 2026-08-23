"""Capability-first Solution creation shared by UI and agent surfaces.

Owner intent is decomposed into a runtime-neutral SolutionManifest before any
legacy runtime is selected. Studio/managed-app are compatibility implementations
while Operly converges on one general Solution runtime.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select

from packages.application_builder.schema import BuilderContext, ProposalRequest
from packages.application_builder.service import ApplicationBuilderService
from packages.database.application_builder_models import ApplicationVersion
from packages.database.product_models import SolutionJob
from packages.solutions.manifest import SolutionManifest, derive_solution_manifest
from packages.solutions.service import LifecycleStatus, RuntimeType, SolutionService, SolutionType
from packages.studio.service import StudioService


@dataclass(frozen=True, slots=True)
class SolutionIntent:
    """Compatibility projection of a capability-first SolutionManifest.

    `solution_type` and `runtime_type` are retained for existing API/UI consumers.
    New orchestration should use the manifest stored on the Solution record.
    """

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


def _compatibility_intent(manifest: SolutionManifest) -> SolutionIntent:
    if manifest.compatibility_runtime == "studio":
        return SolutionIntent(
            SolutionType.DIGITAL_PRESENCE,
            RuntimeType.STUDIO,
            manifest.compatibility_reason,
            "high",
        )
    return SolutionIntent(
        SolutionType.BUSINESS_APP,
        RuntimeType.MANAGED_APP,
        manifest.compatibility_reason,
        "high" if manifest.stateful else "medium",
    )


def classify_solution_intent(name: str, objective: str) -> SolutionIntent:
    """Compatibility API backed by capability decomposition, not product keywords."""
    return _compatibility_intent(derive_solution_manifest(name, objective))


def _context(row) -> dict[str, Any]:
    try:
        value = json.loads(row.context_json or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _log(logs: list[dict[str, Any]], stage: str, status: str, detail: str | None = None) -> None:
    item = {"at": datetime.utcnow().isoformat() + "Z", "stage": stage, "status": status}
    if detail:
        item["detail"] = " ".join(str(detail).split())[:1000]
    logs.append(item)


async def _next_generation_attempt(db, tenant_id: str, solution_id: str) -> int:
    previous = await db.scalar(
        select(SolutionJob)
        .where(
            SolutionJob.tenant_id == tenant_id,
            SolutionJob.solution_id == solution_id,
            SolutionJob.job_type == "initial_generation",
        )
        .order_by(desc(SolutionJob.attempt))
        .limit(1)
    )
    return int(previous.attempt) + 1 if previous else 1


def _builder_message(objective: str, context: dict[str, Any]) -> str:
    """Supply current builders with the minimum capability contract.

    The manifest is architectural truth. The builder may add implementation
    details, but must not satisfy stateful requirements with a static mock.
    """
    manifest = context.get("solutionManifest")
    if not isinstance(manifest, dict):
        return objective
    contract = manifest.get("builderContract")
    if not isinstance(contract, dict):
        return objective
    return (
        objective
        + "\n\nOPERLY SOLUTION ARCHITECTURE CONTRACT (minimum required behavior):\n"
        + json.dumps(contract, ensure_ascii=False, sort_keys=True)
    )[:12000]


async def _run_managed_generation(
    db,
    *,
    tenant_id: str,
    user_id: str,
    row,
    app,
    base_version: ApplicationVersion,
):
    context = _context(row)
    owner = context.get("ownerIntent") if isinstance(context.get("ownerIntent"), dict) else {}
    objective = " ".join(str(owner.get("objective") or row.description or "").split()).strip()[:8000]
    if not objective:
        raise ValueError("The stored Solution creation objective is missing")

    manifest = context.get("solutionManifest") if isinstance(context.get("solutionManifest"), dict) else None
    attempt = await _next_generation_attempt(db, tenant_id, row.id)
    logs: list[dict[str, Any]] = []
    _log(logs, "objective", "succeeded", "Stored owner objective and capability manifest loaded")
    _log(logs, "runtime_bootstrap", "succeeded", f"Managed app bootstrap version {base_version.version_number}")
    evidence: dict[str, Any] = {
        "objective": objective,
        "bootstrapVersionId": base_version.id,
    }
    if manifest:
        evidence["solutionManifest"] = manifest
    job = SolutionJob(
        tenant_id=tenant_id,
        solution_id=row.id,
        source_version_reference=base_version.id,
        job_type="initial_generation",
        status="running",
        attempt=attempt,
        started_at=datetime.utcnow(),
        log_json=json.dumps(logs, ensure_ascii=False),
        evidence_json=json.dumps(evidence, ensure_ascii=False),
        idempotency_key=f"solution:{row.id}:initial-generation:{attempt}",
    )
    db.add(job)
    await db.flush()
    stage = "proposal"
    context["initialGeneration"] = {
        "status": "running",
        "stage": stage,
        "jobId": job.id,
        "attempt": attempt,
        "bootstrapVersionId": base_version.id,
    }
    row.lifecycle_status = LifecycleStatus.BUILDING
    row.current_version_reference = None
    row.preview_state = "unavailable"
    row.preview_url = None
    row.context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
    await db.flush()

    try:
        _log(logs, stage, "running", "Generating a validated change set from the capability contract")
        job.log_json = json.dumps(logs, ensure_ascii=False)
        change = await ApplicationBuilderService.propose(
            db,
            tenant_id,
            user_id,
            "owner",
            ProposalRequest(
                message=_builder_message(objective, context),
                context=BuilderContext(
                    workspaceId=tenant_id,
                    applicationId=app.id,
                    activeVersionId=base_version.id,
                    userRole="owner",
                    selectionScope="application",
                ),
            ),
        )
        _log(logs, stage, "succeeded", f"Change set {change.id} validated")
        stage = "apply"
        _log(logs, stage, "running", "Applying the generated validated manifest")
        job.log_json = json.dumps(logs, ensure_ascii=False)
        version = await ApplicationBuilderService.apply(
            db,
            tenant_id,
            user_id,
            "owner",
            change.id,
        )
        _log(logs, stage, "succeeded", f"Generated version {version.version_number} applied")
        _log(logs, "preview_readiness", "succeeded", "A non-bootstrap generated version is active")
        context["initialGeneration"] = {
            "changeSetId": change.id,
            "versionId": version.id,
            "bootstrapVersionId": base_version.id,
            "jobId": job.id,
            "attempt": attempt,
            "stage": "preview_readiness",
            "status": "applied",
        }
        row.lifecycle_status = LifecycleStatus.PREVIEW_READY
        row.current_version_reference = version.id
        row.preview_state = "ready"
        row.preview_url = "/api/solutions/{solution_id}/preview"
        row.context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
        job.status = "succeeded"
        job.ended_at = datetime.utcnow()
        job.log_json = json.dumps(logs, ensure_ascii=False)
        success_evidence = {
            "objective": objective,
            "bootstrapVersionId": base_version.id,
            "changeSetId": change.id,
            "versionId": version.id,
        }
        if manifest:
            success_evidence["solutionManifest"] = manifest
        job.evidence_json = json.dumps(success_evidence, ensure_ascii=False)
        job.failure_classification = None
        await db.flush()
        return row
    except Exception as error:
        safe_error = " ".join(str(error).split())[:1000] or type(error).__name__
        _log(logs, stage, "failed", safe_error)
        context["initialGeneration"] = {
            "status": "retryable",
            "stage": stage,
            "error": safe_error,
            "jobId": job.id,
            "attempt": attempt,
            "bootstrapVersionId": base_version.id,
        }
        row.lifecycle_status = LifecycleStatus.FAILED
        row.current_version_reference = None
        row.preview_state = "unavailable"
        row.preview_url = None
        row.context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
        job.status = "failed"
        job.ended_at = datetime.utcnow()
        job.log_json = json.dumps(logs, ensure_ascii=False)
        job.failure_classification = type(error).__name__[:80]
        failed_evidence = {
            "objective": objective,
            "bootstrapVersionId": base_version.id,
            "failedStage": stage,
        }
        if manifest:
            failed_evidence["solutionManifest"] = manifest
        job.evidence_json = json.dumps(failed_evidence, ensure_ascii=False)
        await db.flush()
        return row


async def retry_solution_initial_generation(
    db,
    *,
    tenant_id: str,
    user_id: str,
    solution_id: str,
    service: SolutionService | None = None,
):
    service = service or SolutionService()
    row, runtime = await service.resolve(db, tenant_id, solution_id)
    if row.runtime_type != RuntimeType.MANAGED_APP:
        raise ValueError("Only Solutions using the managed compatibility runtime have this generation lifecycle")
    if row.preview_state == "ready" and row.lifecycle_status == LifecycleStatus.PREVIEW_READY:
        raise ValueError("This Solution already has a generated preview-ready version")
    if not runtime.active_version_id:
        raise ValueError("The managed application bootstrap version is missing")
    base_version = await db.get(ApplicationVersion, runtime.active_version_id)
    if not base_version or base_version.application_id != runtime.id or base_version.tenant_id != tenant_id:
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
    """Create a Solution from its capability graph, then bind a compatibility runtime."""
    service = service or SolutionService()
    manifest = derive_solution_manifest(name, objective)
    decision = _compatibility_intent(manifest)
    clean_name = manifest.name
    clean_objective = manifest.objective
    manifest_payload = manifest.as_dict()
    manifest_payload["builderContract"] = manifest.builder_contract()

    context: dict[str, Any] = {
        "ownerIntent": {"name": clean_name, "objective": clean_objective},
        "solutionManifest": manifest_payload,
        "creationIntent": {
            "name": clean_name,
            "objective": clean_objective,
            "classification": decision.as_dict(),
            "compatibilityOnly": True,
        },
        "contextAuthority": ["ownerIntent", "solutionManifest", "solution", "workspaceInherited"],
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
    context["initialGeneration"] = {
        "status": "pending",
        "stage": "runtime_bootstrap",
        "bootstrapVersionId": version.id,
    }
    row = await service._record(
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
        context_json=json.dumps(context, ensure_ascii=False, sort_keys=True),
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
