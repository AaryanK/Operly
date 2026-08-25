"""Canonical Solution creation backed only by SoftwareProject.

All user-facing software creation now enters the same durable SoftwareProject +
AgentRuntime generation path. Historical Studio, ManagedApplication and
GeneratedProject runtimes are not implementation choices anymore.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from packages.software_projects import ProjectState, SoftwareProjectService
from packages.solutions.generation_worker import queue_software_generation
from packages.solutions.manifest import SolutionManifest, derive_solution_manifest
from packages.solutions.service import LifecycleStatus, RuntimeType, SolutionService, SolutionType


@dataclass(frozen=True, slots=True)
class SolutionIntent:
    solution_type: str
    runtime_type: str
    reason: str
    confidence: str
    implementation_mode: str = "software_project"
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


def _implementation_intent(manifest: SolutionManifest) -> SolutionIntent:
    return SolutionIntent(
        solution_type=SolutionType.CUSTOM_SOLUTION,
        runtime_type=RuntimeType.SOFTWARE_PROJECT,
        reason="SoftwareProject is the canonical runtime for all constructed Solutions.",
        confidence="high",
    )


def classify_solution_intent(name: str, objective: str) -> SolutionIntent:
    return _implementation_intent(derive_solution_manifest(name, objective))


async def retry_solution_initial_generation(
    db,
    *,
    tenant_id: str,
    user_id: str,
    solution_id: str,
    service: SolutionService | None = None,
):
    service = service or SolutionService()
    row, project = await service.resolve(db, tenant_id, solution_id)
    if row.runtime_type != RuntimeType.SOFTWARE_PROJECT:
        raise ValueError("Legacy Solution runtimes are no longer executable")
    if row.preview_state == "ready" and row.lifecycle_status == LifecycleStatus.PREVIEW_READY:
        raise ValueError("This Solution already has a preview-ready software build")
    row, _ = await queue_software_generation(db, row=row, user_id=user_id)
    await SoftwareProjectService().set_execution_state(
        db,
        workspace_id=tenant_id,
        project_id=project.id,
        state=ProjectState.BUILDING,
    )
    return row


async def create_solution_from_intent(
    db,
    *,
    tenant_id: str,
    user_id: str,
    name: str,
    objective: str,
    service: SolutionService | None = None,
):
    """Create every constructed Solution as a canonical SoftwareProject."""
    service = service or SolutionService()
    manifest = derive_solution_manifest(name, objective)
    decision = _implementation_intent(manifest)
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
        "sourceAuthority": "software_source_versions",
    }

    projects = SoftwareProjectService()
    project = await projects.create(
        db,
        workspace_id=tenant_id,
        user_id=user_id,
        name=clean_name,
        description=clean_objective,
        metadata={
            "solutionManifest": manifest_payload,
            "implementationResolution": decision.as_dict(),
        },
    )
    record = await projects.record(db, tenant_id, project.id)
    row = await service.create_software_solution(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        project=record,
        objective=clean_objective,
        context=context,
    )
    row, _ = await queue_software_generation(db, row=row, user_id=user_id)
    await projects.set_execution_state(
        db,
        workspace_id=tenant_id,
        project_id=project.id,
        state=ProjectState.BUILDING,
    )
    return row, decision
