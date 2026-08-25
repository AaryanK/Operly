"""Canonical SoftwareProject persistence over current runtime implementations."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import desc, select

from packages.database.application_builder_models import ManagedApplication
from packages.database.custom_software_models import GeneratedProject, GeneratedSourceBundle, RunnerBuildRecord
from packages.database.software_project_models import ServiceBindingRecord, SoftwareProjectRecord
from packages.database.studio_models import StudioProject
from packages.database.studio_source_models import StudioSourceVersion
from packages.software_projects.adapters import (
    from_generated_project,
    from_managed_application,
    from_studio_project,
)
from packages.software_projects.contracts import ProjectState, SoftwareProject


_LEGACY_SOURCES = (
    ("studio", StudioProject, from_studio_project),
    ("managed_app", ManagedApplication, from_managed_application),
    ("generated_project", GeneratedProject, from_generated_project),
)
_FAILED_BUILD_STATES = {
    "failed",
    "build_failed",
    "tests_failed",
    "start_failed",
    "health_check_failed",
    "acceptance_failed",
    "provision_failed",
    "dependency_failed",
    "static_analysis_failed",
    "repair_failed",
    "cancelled",
    "timed_out",
    "security_blocked",
    "resource_exceeded",
}


def _json(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class SoftwareProjectService:
    """Canonical project identity with non-destructive legacy synchronization.

    Existing runtime tables keep owning implementation-specific behavior during
    migration. This service creates/updates stable ``software_projects`` identities
    and projects their latest real source/runtime state into the canonical record.
    """

    async def _binding_ids(self, db, project_id: str) -> tuple[str, ...]:
        rows = (
            await db.scalars(
                select(ServiceBindingRecord.id)
                .where(
                    ServiceBindingRecord.project_id == project_id,
                    ServiceBindingRecord.status == "active",
                )
                .order_by(ServiceBindingRecord.created_at)
            )
        ).all()
        return tuple(str(item) for item in rows)

    async def _as_project(self, db, row: SoftwareProjectRecord) -> SoftwareProject:
        metadata = _json(row.metadata_json)
        if row.legacy_runtime_type:
            metadata.setdefault("compatibility_runtime", row.legacy_runtime_type)
        if row.legacy_runtime_reference:
            metadata.setdefault("runtime_reference", row.legacy_runtime_reference)
        try:
            state = ProjectState(row.state)
        except ValueError:
            state = ProjectState.DRAFT
        return SoftwareProject(
            id=row.id,
            workspace_id=row.tenant_id,
            name=row.name,
            description=row.description or "",
            state=state,
            active_source_version_id=row.active_source_version_id,
            active_runtime_id=row.active_runtime_id,
            service_binding_ids=await self._binding_ids(db, row.id),
            created_by=row.created_by or "",
            created_at=row.created_at,
            updated_at=row.updated_at,
            metadata=metadata,
        )

    async def _project_latest_runtime_state(
        self,
        db,
        runtime_type: str,
        legacy_row,
        projected: SoftwareProject,
    ) -> SoftwareProject:
        if runtime_type == "studio":
            source = await db.scalar(
                select(StudioSourceVersion)
                .where(
                    StudioSourceVersion.tenant_id == projected.workspace_id,
                    StudioSourceVersion.project_id == legacy_row.id,
                )
                .order_by(desc(StudioSourceVersion.source_version))
            )
            if source is not None:
                projected.active_source_version_id = source.id
                projected.active_runtime_id = "static-web-js"
                if source.status == "published" or str(getattr(legacy_row, "status", "")).lower() == "published":
                    projected.state = ProjectState.LIVE
                elif projected.state == ProjectState.DRAFT:
                    projected.state = ProjectState.PREVIEW_READY
            return projected

        if runtime_type == "generated_project":
            plan_id = getattr(legacy_row, "plan_id", None)
            plan_version = getattr(legacy_row, "approved_plan_version", None)
            if plan_id and plan_version:
                source = await db.scalar(
                    select(GeneratedSourceBundle)
                    .where(
                        GeneratedSourceBundle.tenant_id == projected.workspace_id,
                        GeneratedSourceBundle.plan_id == plan_id,
                        GeneratedSourceBundle.plan_version == plan_version,
                    )
                    .order_by(desc(GeneratedSourceBundle.source_version))
                )
                if source is not None:
                    projected.active_source_version_id = source.id
                    provenance = _json(source.provenance_json)
                    projected.active_runtime_id = (
                        str(provenance.get("detectedRuntimeProfile") or "generated-runtime")
                    )
                    build = await db.scalar(
                        select(RunnerBuildRecord)
                        .where(
                            RunnerBuildRecord.tenant_id == projected.workspace_id,
                            RunnerBuildRecord.source_bundle_id == source.id,
                        )
                        .order_by(desc(RunnerBuildRecord.created_at))
                    )
                    if build is None:
                        projected.state = ProjectState.BUILDING
                    elif str(build.state) == "preview_ready":
                        projected.state = ProjectState.PREVIEW_READY
                    elif str(build.state) in _FAILED_BUILD_STATES:
                        projected.state = ProjectState.FAILED
                    else:
                        projected.state = ProjectState.BUILDING
                    projected.metadata.update(
                        {
                            "latest_build_id": getattr(build, "id", None) if build is not None else None,
                            "latest_build_state": getattr(build, "state", None) if build is not None else None,
                            "source_bundle_digest": getattr(source, "bundle_digest", None),
                        }
                    )
            return projected

        return projected

    async def _ensure_legacy_record(
        self,
        db,
        *,
        runtime_type: str,
        legacy_row,
        adapter,
    ) -> SoftwareProjectRecord:
        projected = adapter(legacy_row)
        projected = await self._project_latest_runtime_state(
            db,
            runtime_type,
            legacy_row,
            projected,
        )
        row = await db.scalar(
            select(SoftwareProjectRecord).where(
                SoftwareProjectRecord.tenant_id == projected.workspace_id,
                SoftwareProjectRecord.legacy_runtime_type == runtime_type,
                SoftwareProjectRecord.legacy_runtime_reference == str(legacy_row.id),
            )
        )
        metadata = dict(projected.metadata)
        metadata["compatibility_runtime"] = runtime_type
        metadata["runtime_reference"] = str(legacy_row.id)
        values = {
            "name": projected.name,
            "description": projected.description,
            "state": projected.state.value,
            "active_source_version_id": projected.active_source_version_id,
            "active_runtime_id": projected.active_runtime_id,
            "metadata_json": json.dumps(metadata, sort_keys=True, default=str),
            "created_by": projected.created_by,
        }
        if row is None:
            row = SoftwareProjectRecord(
                tenant_id=projected.workspace_id,
                legacy_runtime_type=runtime_type,
                legacy_runtime_reference=str(legacy_row.id),
                created_at=projected.created_at or datetime.utcnow(),
                **values,
            )
            db.add(row)
            await db.flush()
        else:
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = projected.updated_at or datetime.utcnow()
        return row

    async def sync_legacy(self, db, workspace_id: str) -> None:
        for runtime_type, model, adapter in _LEGACY_SOURCES:
            statement = select(model).where(model.tenant_id == workspace_id)
            if model is StudioProject:
                statement = statement.where(StudioProject.status != "archived")
            rows = (await db.scalars(statement)).all()
            for legacy_row in rows:
                await self._ensure_legacy_record(
                    db,
                    runtime_type=runtime_type,
                    legacy_row=legacy_row,
                    adapter=adapter,
                )
        await db.flush()

    async def list(self, db, workspace_id: str) -> list[SoftwareProject]:
        await self.sync_legacy(db, workspace_id)
        rows = (
            await db.scalars(
                select(SoftwareProjectRecord)
                .where(
                    SoftwareProjectRecord.tenant_id == workspace_id,
                    SoftwareProjectRecord.state != ProjectState.ARCHIVED.value,
                )
                .order_by(SoftwareProjectRecord.updated_at.desc())
            )
        ).all()
        return [await self._as_project(db, row) for row in rows]

    async def record(self, db, workspace_id: str, project_id: str) -> SoftwareProjectRecord:
        await self.sync_legacy(db, workspace_id)
        row = await db.scalar(
            select(SoftwareProjectRecord).where(
                SoftwareProjectRecord.id == project_id,
                SoftwareProjectRecord.tenant_id == workspace_id,
            )
        )
        if row is None:
            # Compatibility lookup while UI/API surfaces are still migrating from
            # legacy runtime ids to canonical project ids.
            row = await db.scalar(
                select(SoftwareProjectRecord).where(
                    SoftwareProjectRecord.tenant_id == workspace_id,
                    SoftwareProjectRecord.legacy_runtime_reference == project_id,
                )
            )
        if row is None:
            raise LookupError("Software project not found")
        return row

    async def get(self, db, workspace_id: str, project_id: str) -> SoftwareProject:
        return await self._as_project(
            db,
            await self.record(db, workspace_id, project_id),
        )

    async def create(
        self,
        db,
        *,
        workspace_id: str,
        user_id: str,
        name: str,
        description: str = "",
        metadata: dict | None = None,
    ) -> SoftwareProject:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("Project name is required")
        row = SoftwareProjectRecord(
            tenant_id=workspace_id,
            name=clean_name[:200],
            description=str(description or "")[:8000],
            state=ProjectState.DRAFT.value,
            metadata_json=json.dumps(metadata or {}, sort_keys=True, default=str),
            created_by=user_id,
        )
        db.add(row)
        await db.flush()
        return await self._as_project(db, row)

    async def set_execution_state(
        self,
        db,
        *,
        workspace_id: str,
        project_id: str,
        source_version_id: str | None = None,
        runtime_id: str | None = None,
        state: ProjectState | None = None,
    ) -> SoftwareProject:
        row = await self.record(db, workspace_id, project_id)
        if source_version_id is not None:
            row.active_source_version_id = source_version_id
        if runtime_id is not None:
            row.active_runtime_id = runtime_id
        if state is not None:
            row.state = state.value
        row.updated_at = datetime.utcnow()
        await db.flush()
        return await self._as_project(db, row)

    async def legacy_target(
        self,
        db,
        workspace_id: str,
        project_id: str,
    ) -> tuple[str, str] | None:
        row = await self.record(db, workspace_id, project_id)
        if not row.legacy_runtime_type or not row.legacy_runtime_reference:
            return None
        return row.legacy_runtime_type, row.legacy_runtime_reference
