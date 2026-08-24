"""Capability-first Solution creation shared by UI and agent surfaces.

Owner intent is decomposed into a runtime-neutral SolutionManifest before an
implementation target is selected. Studio and managed-app are fast declarative
compiler targets; requests outside their finite envelope fall through to the
isolated generated full-stack runtime and must earn preview readiness through a
real build/test/health/acceptance path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select

from packages.application_builder.schema import BuilderContext, ProposalRequest
from packages.application_builder.service import ApplicationBuilderService
from packages.coding_harness.execution_loop import build_with_repair
from packages.custom_software.plan_service import approve, create_plan, owned_plan, plan_version
from packages.custom_software.runner_adapters import ExternalRunnerAdapter
from packages.custom_software.service import create_project_from_plan
from packages.database.application_builder_models import ApplicationVersion
from packages.database.product_models import SolutionJob
from packages.solutions.implementation import resolve_solution_implementation
from packages.solutions.manifest import SolutionManifest, derive_solution_manifest
from packages.solutions.service import LifecycleStatus, RuntimeType, SolutionService, SolutionType
from packages.studio.service import StudioService


@dataclass(frozen=True, slots=True)
class SolutionIntent:
    """Compatibility projection plus truthful implementation selection."""

    solution_type: str
    runtime_type: str
    reason: str
    confidence: str
    implementation_mode: str = "compatibility"
    required_capabilities: tuple[str, ...] = ()
    generated_capabilities: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "solutionType": self.solution_type,
            "runtimeType": self.runtime_type,
            "reason": self.reason,
            "confidence": self.confidence,
            "implementationMode": self.implementation_mode,
            "requiredCapabilities": list(self.required_capabilities),
            "generatedCapabilities": list(self.generated_capabilities),
        }


def _implementation_intent(
    manifest: SolutionManifest,
    *,
    name: str | None = None,
    objective: str | None = None,
) -> SolutionIntent:
    resolution = resolve_solution_implementation(
        manifest,
        name=name or manifest.name,
        objective=objective or manifest.objective,
    )
    return SolutionIntent(
        solution_type=SolutionType(resolution.solution_type),
        runtime_type=RuntimeType(resolution.runtime_type),
        reason=resolution.reason,
        confidence=resolution.confidence,
        implementation_mode=resolution.implementation_mode,
        required_capabilities=resolution.required_capabilities,
        generated_capabilities=resolution.generated_capabilities,
    )


def classify_solution_intent(name: str, objective: str) -> SolutionIntent:
    """Compatibility API backed by capability decomposition and coverage truth."""
    manifest = derive_solution_manifest(name, objective)
    return _implementation_intent(manifest, name=name, objective=objective)


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
    """Supply declarative builders with the minimum capability contract."""
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


def _planning_prompt(name: str, objective: str, context: dict[str, Any]) -> str:
    """Give the general software planner semantic truth without runtime micromanagement."""
    implementation = context.get("implementationResolution")
    architecture = context.get("solutionManifest")
    payload = {
        "name": name,
        "objective": objective,
        "solutionManifest": architecture if isinstance(architecture, dict) else {},
        "implementationResolution": implementation if isinstance(implementation, dict) else {},
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


async def _run_generated_generation(
    db,
    *,
    tenant_id: str,
    user_id: str,
    row,
    project,
    software_plan_row,
    software_plan,
):
    """Generate, execute and verify arbitrary source before claiming preview readiness."""
    context = _context(row)
    owner = context.get("ownerIntent") if isinstance(context.get("ownerIntent"), dict) else {}
    objective = " ".join(str(owner.get("objective") or row.description or "").split()).strip()[:8000]
    attempt = await _next_generation_attempt(db, tenant_id, row.id)
    logs: list[dict[str, Any]] = []
    _log(logs, "objective", "succeeded", "Validated SoftwarePlan and owner objective loaded")
    _log(logs, "capability_resolution", "succeeded", "Uncovered capabilities assigned to generated full-stack source")
    job = SolutionJob(
        tenant_id=tenant_id,
        solution_id=row.id,
        source_version_reference=None,
        job_type="initial_generation",
        status="running",
        attempt=attempt,
        started_at=datetime.utcnow(),
        log_json=json.dumps(logs, ensure_ascii=False),
        evidence_json=json.dumps(
            {
                "objective": objective,
                "softwarePlanId": software_plan_row.id,
                "softwarePlanVersion": software_plan_row.approved_version,
                "implementationResolution": context.get("implementationResolution", {}),
            },
            ensure_ascii=False,
        ),
        idempotency_key=f"solution:{row.id}:generated-build:{attempt}",
    )
    db.add(job)
    await db.flush()
    stage = "source_generation"
    context["initialGeneration"] = {
        "status": "running",
        "stage": stage,
        "jobId": job.id,
        "attempt": attempt,
        "softwarePlanId": software_plan_row.id,
        "softwarePlanVersion": software_plan_row.approved_version,
    }
    row.lifecycle_status = LifecycleStatus.BUILDING
    row.current_version_reference = None
    row.preview_state = "unavailable"
    row.preview_url = None
    row.context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
    await db.flush()

    try:
        _log(logs, stage, "running", "Generating executable source from the validated requirement ledger")
        job.log_json = json.dumps(logs, ensure_ascii=False)
        build, source, repairs = await build_with_repair(
            db,
            tenant_id,
            user_id,
            software_plan_row,
            software_plan,
            job.idempotency_key,
            adapter=ExternalRunnerAdapter(),
        )
        job.source_version_reference = str(source.source_version)
        stage = "acceptance_test"
        if build.state != "preview_ready":
            classification = build.failure_classification or build.state or "generated_build_failed"
            raise RuntimeError(f"Generated build did not reach preview_ready: {classification}")

        _log(logs, "build", "succeeded", f"Isolated build {build.id} completed")
        _log(logs, "acceptance_test", "succeeded", "Runner build, tests, health checks and acceptance checks passed")
        _log(logs, "preview_readiness", "succeeded", "Verified isolated runner preview is active")
        context["initialGeneration"] = {
            "status": "applied",
            "stage": "preview_readiness",
            "jobId": job.id,
            "attempt": attempt,
            "softwarePlanId": software_plan_row.id,
            "softwarePlanVersion": software_plan_row.approved_version,
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
        job.log_json = json.dumps(logs, ensure_ascii=False)
        job.failure_classification = None
        job.evidence_json = json.dumps(
            {
                "objective": objective,
                "softwarePlanId": software_plan_row.id,
                "softwarePlanVersion": software_plan_row.approved_version,
                "sourceBundleId": source.id,
                "sourceVersion": source.source_version,
                "buildId": build.id,
                "buildState": build.state,
                "repairs": repairs,
                "implementationResolution": context.get("implementationResolution", {}),
            },
            ensure_ascii=False,
        )
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
            "softwarePlanId": software_plan_row.id,
            "softwarePlanVersion": software_plan_row.approved_version,
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
        job.evidence_json = json.dumps(
            {
                "objective": objective,
                "softwarePlanId": software_plan_row.id,
                "softwarePlanVersion": software_plan_row.approved_version,
                "failedStage": stage,
                "implementationResolution": context.get("implementationResolution", {}),
            },
            ensure_ascii=False,
        )
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
    if row.preview_state == "ready" and row.lifecycle_status == LifecycleStatus.PREVIEW_READY:
        raise ValueError("This Solution already has a generated preview-ready version")

    if row.runtime_type == RuntimeType.MANAGED_APP:
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

    if row.runtime_type == RuntimeType.GENERATED_PROJECT:
        if not runtime.plan_id or not runtime.approved_plan_version:
            raise ValueError("The generated Solution is not bound to an approved SoftwarePlan")
        software_plan_row = await owned_plan(db, tenant_id, runtime.plan_id)
        _, software_plan = await plan_version(db, software_plan_row, runtime.approved_plan_version)
        return await _run_generated_generation(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            row=row,
            project=runtime,
            software_plan_row=software_plan_row,
            software_plan=software_plan,
        )

    raise ValueError("This Solution runtime does not have an initial generation retry lifecycle")


async def create_solution_from_intent(
    db,
    *,
    tenant_id: str,
    user_id: str,
    name: str,
    objective: str,
    service: SolutionService | None = None,
):
    """Create a Solution from capability truth, using generation when required."""
    service = service or SolutionService()
    manifest = derive_solution_manifest(name, objective)
    decision = _implementation_intent(manifest, name=name, objective=objective)
    clean_name = manifest.name
    clean_objective = manifest.objective
    manifest_payload = manifest.as_dict()
    manifest_payload["builderContract"] = manifest.builder_contract()

    context: dict[str, Any] = {
        "ownerIntent": {"name": clean_name, "objective": clean_objective},
        "solutionManifest": manifest_payload,
        "implementationResolution": decision.as_dict(),
        "creationIntent": {
            "name": clean_name,
            "objective": clean_objective,
            "classification": decision.as_dict(),
            "compatibilityOnly": False,
        },
        "contextAuthority": [
            "ownerIntent",
            "solutionManifest",
            "implementationResolution",
            "solution",
            "workspaceInherited",
        ],
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

    if decision.runtime_type == RuntimeType.GENERATED_PROJECT:
        try:
            software_plan_row, software_plan_version, software_plan = await create_plan(
                db,
                tenant_id,
                user_id,
                _planning_prompt(clean_name, clean_objective, context),
            )
            await approve(db, software_plan_row, software_plan_version.version)
            project = await create_project_from_plan(
                db,
                tenant_id,
                user_id,
                software_plan_row,
                software_plan,
            )
        except Exception as error:
            safe_error = " ".join(str(error).split())[:1000] or type(error).__name__
            raise ValueError(f"Generated Solution planning failed: {safe_error}") from error

        context["softwarePlan"] = {
            "id": software_plan_row.id,
            "version": software_plan_row.approved_version,
            "status": software_plan_row.status,
        }
        context["initialGeneration"] = {
            "status": "pending",
            "stage": "source_generation",
            "softwarePlanId": software_plan_row.id,
            "softwarePlanVersion": software_plan_row.approved_version,
        }
        row = await service._record(
            db,
            tenant_id,
            RuntimeType.GENERATED_PROJECT,
            project.id,
            name=clean_name,
            description=clean_objective,
            solution_type=SolutionType.CUSTOM_SOLUTION,
            lifecycle_status=LifecycleStatus.BUILDING,
            current_version_reference=None,
            preview_state="unavailable",
            preview_url=None,
            production_state="offline",
            production_url=None,
            visibility="private",
            context_json=json.dumps(context, ensure_ascii=False, sort_keys=True),
        )
        await _run_generated_generation(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            row=row,
            project=project,
            software_plan_row=software_plan_row,
            software_plan=software_plan,
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
