"""High-level AgentRuntime surface for durable source-first software generation.

The outer model gets a tiny project-level vocabulary. It never receives the coding
agent's filesystem/terminal/browser subtools. Those remain inside the coding harness
and isolated runner. The capability starts or observes durable work; it never treats
queue acceptance as proof that the requested application works.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select

from packages.artifacts.service import ArtifactScope, ArtifactService, artifact_json
from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.database.custom_software_models import GeneratedProject
from packages.database.product_models import SolutionJob, SolutionRecord
from packages.software_projects import SoftwareProjectService
from packages.software_projects.delivery import persist_generated_source_archive
from packages.solutions.composer import create_solution_from_intent
from packages.solutions.service import LifecycleStatus, RuntimeType, SolutionService, solution_json


def _json(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _project_json(project) -> dict[str, Any]:
    return {
        "id": project.id,
        "workspace_id": project.workspace_id,
        "name": project.name,
        "description": project.description,
        "state": project.state.value,
        "active_source_version_id": project.active_source_version_id,
        "active_runtime_id": project.active_runtime_id,
        "service_binding_ids": list(project.service_binding_ids),
        "metadata": dict(project.metadata),
    }


def _default_name(objective: str) -> str:
    text = " ".join(str(objective or "").replace("\x00", "").split()).strip()
    if not text:
        return "Software Project"
    sentence = text.split(".", 1)[0].strip()
    return (sentence[:80].rstrip(" ,;:-") or "Software Project")


def _job_evidence(job: SolutionJob | None) -> dict[str, Any]:
    if job is None:
        return {}
    return _json(job.evidence_json)


def _job_json(job: SolutionJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    evidence = _job_evidence(job)
    return {
        "id": job.id,
        "status": job.status,
        "attempt": job.attempt,
        "plan_id": job.plan_id,
        "source_version_reference": job.source_version_reference,
        "failure_classification": job.failure_classification,
        "build_id": evidence.get("buildId"),
        "build_state": evidence.get("buildState"),
        "source_bundle_id": evidence.get("sourceBundleId"),
        "source_version": evidence.get("sourceVersion"),
        "source_archive_artifact_id": evidence.get("sourceArchiveArtifactId"),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "ended_at": job.ended_at.isoformat() if job.ended_at else None,
    }


async def _latest_job(db, solution_id: str) -> SolutionJob | None:
    return await db.scalar(
        select(SolutionJob)
        .where(SolutionJob.solution_id == solution_id)
        .order_by(desc(SolutionJob.attempt), desc(SolutionJob.created_at))
        .limit(1)
    )


def _generated_runner_verified(job: SolutionJob | None) -> bool:
    evidence = _job_evidence(job)
    return bool(
        job is not None
        and job.status == "succeeded"
        and str(evidence.get("buildState") or "") == "preview_ready"
        and evidence.get("buildId")
        and evidence.get("sourceBundleId")
        and evidence.get("sourceVersion") is not None
    )


async def _archive_from_job(db, tenant_id: str, job: SolutionJob | None) -> dict[str, Any] | None:
    artifact_id = str(_job_evidence(job).get("sourceArchiveArtifactId") or "").strip()
    if not artifact_id:
        return None
    try:
        row = await ArtifactService(db).get(
            ArtifactScope("workspace", tenant_id, tenant_id=tenant_id),
            artifact_id,
        )
    except LookupError:
        return None
    return artifact_json(row)


class SoftwareBuildProvider(BaseProvider):
    """One governed software-building seam shared by AI, Studio, workflows and MCP."""

    name = "operly_software_build"
    capabilities = (
        CapabilityDefinition(
            "software.build",
            "software_build",
            (
                "Start a private durable software build for an arbitrary runnable application or complete codebase. "
                "Operly owns planning, coding, source versioning, isolated build/test/start/health/acceptance checks and bounded repair internally. "
                "The operation creates no public deployment and exposes none of the coding agent's low-level tools to the caller."
            ),
            {
                "type": "object",
                "properties": {
                    "objective": {"type": "string", "minLength": 1, "maxLength": 12000},
                    "name": {"type": "string", "minLength": 1, "maxLength": 200},
                    "return_source_archive": {"type": "boolean", "default": True},
                },
                "required": ["objective"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("solution:generate",),
            approval_policy=ApprovalPolicy.AUTO,
            reversible=True,
            plugin_id="operly.software",
            category="software",
            display_name="Build software",
            tags=frozenset({"software", "application", "codebase", "source", "studio", "build", "agent"}),
            semantic_operations=frozenset(
                {
                    "build an application",
                    "build complete software",
                    "create a working codebase",
                    "generate runnable source code",
                    "build and test software",
                    "create app source files",
                    "build software project for studio",
                }
            ),
        ),
        CapabilityDefinition(
            "software.build.status",
            "software_build_status",
            "Inspect durable software-build progress and provider-verified source/build/test/start/health/acceptance evidence for one canonical SoftwareProject.",
            {
                "type": "object",
                "properties": {"project_id": {"type": "string", "minLength": 1, "maxLength": 36}},
                "required": ["project_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("solution:read",),
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="operly.software",
            category="software",
            display_name="Inspect software build",
            tags=frozenset({"software", "build", "status", "verification", "studio"}),
            semantic_operations=frozenset({"check software build", "inspect app build progress", "verify generated application"}),
        ),
        CapabilityDefinition(
            "software.source.export",
            "software_source_export",
            (
                "Export the current immutable generated source bundle for a SoftwareProject as a durable ZIP artifact. "
                "The ZIP is a delivery projection only; it never becomes authoritative source and its contents are never executed by this capability."
            ),
            {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "minLength": 1, "maxLength": 36},
                    "filename": {"type": "string", "minLength": 1, "maxLength": 255},
                },
                "required": ["project_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("solution:read", "files:process"),
            approval_policy=ApprovalPolicy.AUTO,
            reversible=True,
            plugin_id="operly.software",
            category="software",
            display_name="Export software source",
            tags=frozenset({"software", "source", "zip", "download", "artifact", "codebase"}),
            semantic_operations=frozenset({"download source code", "export codebase", "create source zip", "give me project files"}),
        ),
    )

    def __init__(self) -> None:
        self.projects = SoftwareProjectService()
        self.solutions = SolutionService()

    async def _project_solution(self, context, project_id: str):
        project = await self.projects.get(context.db, context.tenant_id, project_id)
        target = await self.projects.legacy_target(context.db, context.tenant_id, project.id)
        if target is None:
            return project, None
        runtime_type, runtime_reference = target
        solution = await context.db.scalar(
            select(SolutionRecord).where(
                SolutionRecord.tenant_id == context.tenant_id,
                SolutionRecord.runtime_type == runtime_type,
                SolutionRecord.runtime_reference == runtime_reference,
            )
        )
        return project, solution

    async def _status_payload(self, context, project_id: str) -> dict[str, Any]:
        await self.solutions.sync(context.db, context.tenant_id)
        await self.projects.sync_legacy(context.db, context.tenant_id)
        project, solution = await self._project_solution(context, project_id)
        if solution is None:
            return {
                "project": _project_json(project),
                "project_id": project.id,
                "build_state": project.state.value,
                "build_success": False,
                "preview_available": project.state.value == "preview_ready",
                "reason": "project_has_no_solution_build_lifecycle",
            }

        job = await _latest_job(context.db, solution.id)
        generated = str(solution.runtime_type) == RuntimeType.GENERATED_PROJECT.value
        runner_verified = generated and _generated_runner_verified(job)
        archive = await _archive_from_job(context.db, context.tenant_id, job)
        payload: dict[str, Any] = {
            "project": _project_json(project),
            "project_id": project.id,
            "solution": solution_json(solution),
            "solution_id": solution.id,
            "job": _job_json(job),
            "job_id": job.id if job else None,
            "lifecycle_status": str(solution.lifecycle_status),
            "build_state": (_job_evidence(job).get("buildState") if job else None) or str(solution.lifecycle_status),
            "source_bundle_id": _job_evidence(job).get("sourceBundleId") if job else None,
            "source_version": _job_evidence(job).get("sourceVersion") if job else None,
            "build_id": _job_evidence(job).get("buildId") if job else None,
            "preview_available": solution.preview_state == "ready",
            "preview_url": solution_json(solution).get("preview", {}).get("url"),
            "production_state": solution.production_state,
            "production_url": solution.production_url,
            "private_preview_only": solution.production_state != "live",
            "publication_performed": solution.production_state == "live",
            "source_archive": archive,
            "source_archive_artifact_id": archive.get("artifact_id") if archive else None,
            # The generated worker reaches preview_ready only after its isolated
            # build/test/process-start/health/acceptance contract passes. Normalize
            # that durable worker result into the evidence vocabulary used by the
            # root-objective verifier. Never infer these booleans from model prose.
            "build_success": bool(runner_verified),
            "test_success": bool(runner_verified),
            "tests_passed": bool(runner_verified),
            "process_start_success": bool(runner_verified),
            "process_started": bool(runner_verified),
            "health_check_success": bool(runner_verified),
            "health_passed": bool(runner_verified),
            "acceptance_check_success": bool(runner_verified),
            "acceptance_passed": bool(runner_verified),
        }
        if job and job.failure_classification:
            payload["failure_classification"] = job.failure_classification
            payload["failure_evidence"] = _job_evidence(job)
        return payload

    async def execute(self, context, capability_name, arguments):
        if capability_name == "software.build":
            if not context.actor_id:
                return CapabilityResult(False, False, {"reason": "authenticated_actor_required"})
            objective = " ".join(str(arguments.get("objective") or "").replace("\x00", "").split()).strip()[:12000]
            if not objective:
                return CapabilityResult(False, False, {"reason": "objective_required"})
            name = " ".join(str(arguments.get("name") or _default_name(objective)).replace("\x00", "").split()).strip()[:200]
            row, decision = await create_solution_from_intent(
                context.db,
                tenant_id=context.tenant_id,
                user_id=context.actor_id,
                name=name,
                objective=objective,
                service=self.solutions,
            )

            stored_context = _json(row.context_json)
            stored_context["softwareBuildRequest"] = {
                "requestedBy": context.actor_id,
                "returnSourceArchive": bool(arguments.get("return_source_archive", True)),
                "privatePreviewOnly": True,
                "publishAuthorized": False,
            }
            row.context_json = json.dumps(stored_context, ensure_ascii=False, sort_keys=True)
            await self.projects.sync_legacy(context.db, context.tenant_id)
            project = await self.projects.get(context.db, context.tenant_id, str(row.runtime_reference))
            job = await _latest_job(context.db, row.id)
            payload = {
                "project": _project_json(project),
                "project_id": project.id,
                "solution": solution_json(row),
                "solution_id": row.id,
                "classification": decision.as_dict(),
                "job": _job_json(job),
                "job_id": job.id if job else None,
                "job_accepted": bool(job and job.status in {"queued", "running", "succeeded"}) or row.preview_state == "ready",
                "build_state": job.status if job else str(row.lifecycle_status),
                "build_success": False,
                "source_archive_requested": bool(arguments.get("return_source_archive", True)),
                "private_preview_only": True,
                "publication_performed": False,
                "deployment_performed": False,
            }
            # Queue acceptance is the verified result of this capability invocation;
            # it is deliberately not proof of the root software objective.
            return CapabilityResult(True, True, payload, project.id)

        if capability_name == "software.build.status":
            try:
                payload = await self._status_payload(context, str(arguments["project_id"]))
            except LookupError as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            return CapabilityResult(True, False, payload, payload.get("project_id"))

        if capability_name == "software.source.export":
            if not context.actor_id:
                return CapabilityResult(False, False, {"reason": "authenticated_actor_required"})
            try:
                project = await self.projects.get(context.db, context.tenant_id, str(arguments["project_id"]))
                target = await self.projects.legacy_target(context.db, context.tenant_id, project.id)
            except LookupError as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            if target is None or target[0] != RuntimeType.GENERATED_PROJECT.value:
                return CapabilityResult(
                    False,
                    False,
                    {"reason": "source_export_not_yet_supported_for_this_runtime", "project_id": project.id},
                )
            generated = await context.db.scalar(
                select(GeneratedProject).where(
                    GeneratedProject.id == target[1],
                    GeneratedProject.tenant_id == context.tenant_id,
                )
            )
            if generated is None:
                return CapabilityResult(False, False, {"reason": "generated_project_missing"})
            source = await self.solutions.latest_generated_source(
                context.db,
                context.tenant_id,
                generated.plan_id,
                generated.approved_plan_version,
            )
            if source is None:
                return CapabilityResult(False, False, {"reason": "generated_source_not_available_yet"})
            invocation = context.invocation if isinstance(context.invocation, dict) else {}
            metadata = invocation.get("metadata") if isinstance(invocation.get("metadata"), dict) else {}
            filename = str(arguments.get("filename") or f"{project.name}-source-v{source.source_version}.zip")
            artifact = await persist_generated_source_archive(
                context.db,
                tenant_id=context.tenant_id,
                created_by=context.actor_id,
                source=source,
                filename=filename,
                run_id=str(metadata.get("runtime_run_id") or "") or None,
            )
            solution = await context.db.scalar(
                select(SolutionRecord).where(
                    SolutionRecord.tenant_id == context.tenant_id,
                    SolutionRecord.runtime_type == RuntimeType.GENERATED_PROJECT,
                    SolutionRecord.runtime_reference == generated.id,
                )
            )
            job = await _latest_job(context.db, solution.id) if solution else None
            if job is not None:
                evidence = _job_evidence(job)
                evidence.update(
                    {
                        "sourceArchiveArtifactId": artifact["artifact_id"],
                        "sourceArchiveFilename": artifact["filename"],
                        "sourceArchiveSha256": artifact["sha256"],
                    }
                )
                job.evidence_json = json.dumps(evidence, ensure_ascii=False)
            return CapabilityResult(
                True,
                True,
                {
                    "project_id": project.id,
                    "source_bundle_id": source.id,
                    "source_version": source.source_version,
                    "artifact_id": artifact["artifact_id"],
                    "artifact_ids": [artifact["artifact_id"]],
                    "artifacts": [artifact],
                    "artifact_kind": "software_source_archive",
                    "source_archive": artifact,
                    "persisted": True,
                    "projection_only": True,
                    "executed": False,
                },
                artifact["artifact_id"],
            )

        return CapabilityResult(False, False, {"reason": "unsupported_software_build_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if not result.success:
            return CapabilityResult(False, result.changed, result.evidence, result.external_reference)

        if capability_name == "software.build":
            try:
                project = await self.projects.get(
                    context.db,
                    context.tenant_id,
                    str(result.external_reference or ""),
                )
            except LookupError:
                return CapabilityResult(False, result.changed, {"reason": "software_project_not_persisted"})
            solution_id = str(result.evidence.get("solution_id") or "")
            solution = await context.db.scalar(
                select(SolutionRecord).where(
                    SolutionRecord.id == solution_id,
                    SolutionRecord.tenant_id == context.tenant_id,
                )
            )
            if solution is None:
                return CapabilityResult(False, result.changed, {"reason": "software_solution_not_persisted"})
            return CapabilityResult(
                True,
                result.changed,
                {"persisted": True, "project_id": project.id, **result.evidence},
                project.id,
            )

        if capability_name == "software.build.status":
            return CapabilityResult(True, False, {"observed": True, **result.evidence}, result.external_reference)

        if capability_name == "software.source.export":
            artifact_id = str(result.evidence.get("artifact_id") or "")
            if not artifact_id:
                return CapabilityResult(False, result.changed, {"reason": "source_archive_artifact_missing"})
            try:
                row = await ArtifactService(context.db).get(
                    ArtifactScope("workspace", context.tenant_id, tenant_id=context.tenant_id),
                    artifact_id,
                )
            except LookupError:
                return CapabilityResult(False, result.changed, {"reason": "source_archive_not_persisted"})
            return CapabilityResult(
                True,
                result.changed,
                {
                    **result.evidence,
                    "artifact_id": row.id,
                    "persisted": True,
                    "sha256": row.sha256,
                    "filename": row.filename,
                },
                row.id,
            )

        return CapabilityResult(False, result.changed, {"reason": "unsupported_software_build_capability"})


__all__ = ["SoftwareBuildProvider"]
