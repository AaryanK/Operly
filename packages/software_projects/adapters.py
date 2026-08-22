"""Compatibility adapters from existing Operly product generations."""
from __future__ import annotations

from packages.software_projects.contracts import ProjectState, SoftwareProject


def _state(value: str | None, *, ready: bool = False, live: bool = False) -> ProjectState:
    clean = str(value or "").strip().lower()
    if clean == "archived":
        return ProjectState.ARCHIVED
    if clean in {item.value for item in ProjectState}:
        return ProjectState(clean)
    if live:
        return ProjectState.LIVE
    if ready:
        return ProjectState.PREVIEW_READY
    return ProjectState.DRAFT


def from_studio_project(row) -> SoftwareProject:
    ready = bool(getattr(row, "active_draft_version_id", None))
    live = bool(getattr(row, "published_version_id", None))
    return SoftwareProject(
        id=str(row.id),
        workspace_id=str(row.tenant_id),
        name=str(row.name),
        description=str(getattr(row, "description", "") or ""),
        state=_state(getattr(row, "status", None), ready=ready, live=live),
        active_source_version_id=(
            str(getattr(row, "active_draft_version_id", "") or "") or None
        ),
        active_runtime_id="compat:studio-website",
        service_binding_ids=(),
        created_by=str(getattr(row, "created_by", "") or ""),
        created_at=getattr(row, "created_at", None),
        updated_at=getattr(row, "updated_at", None),
        metadata={"compatibility_runtime": "studio", "runtime_reference": str(row.id)},
    )


def from_managed_application(row) -> SoftwareProject:
    active = str(getattr(row, "active_version_id", "") or "") or None
    return SoftwareProject(
        id=str(row.id),
        workspace_id=str(row.tenant_id),
        name=str(row.name),
        description=str(getattr(row, "description", "") or ""),
        state=ProjectState.PREVIEW_READY if active else ProjectState.DRAFT,
        active_source_version_id=active,
        active_runtime_id="compat:managed-application",
        service_binding_ids=(),
        created_by=str(getattr(row, "created_by", "") or ""),
        created_at=getattr(row, "created_at", None),
        updated_at=getattr(row, "updated_at", None),
        metadata={"compatibility_runtime": "managed_app", "runtime_reference": str(row.id)},
    )


def from_generated_project(row) -> SoftwareProject:
    approved = bool(getattr(row, "approved_plan_version", None))
    state = ProjectState.APPROVED if approved else ProjectState.PLANNING
    return SoftwareProject(
        id=str(row.id),
        workspace_id=str(row.tenant_id),
        name=str(row.name),
        description=str(getattr(row, "prompt", "") or "")[:4000],
        state=state,
        active_source_version_id=None,
        active_runtime_id="compat:generated-project",
        service_binding_ids=(),
        created_by=str(getattr(row, "created_by", "") or ""),
        created_at=getattr(row, "created_at", None),
        updated_at=getattr(row, "updated_at", None),
        metadata={
            "compatibility_runtime": "generated_project",
            "runtime_reference": str(row.id),
            "plan_id": getattr(row, "plan_id", None),
            "approved_plan_version": getattr(row, "approved_plan_version", None),
        },
    )
