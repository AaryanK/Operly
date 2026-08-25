"""Canonical SoftwareProject persistence.

SoftwareProject is the only product-level identity for constructed software.
Historical Studio, ManagedApplication and GeneratedProject rows are not discovered,
synchronized or treated as alternate project identities here.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select

from packages.database.software_project_models import ServiceBindingRecord, SoftwareProjectRecord
from packages.software_projects.contracts import ProjectState, SoftwareProject


def _json(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class SoftwareProjectService:
    """Persistence service for the single canonical software-project identity."""

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
            metadata=_json(row.metadata_json),
        )

    async def list(self, db, workspace_id: str) -> list[SoftwareProject]:
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
        row = await db.scalar(
            select(SoftwareProjectRecord).where(
                SoftwareProjectRecord.id == project_id,
                SoftwareProjectRecord.tenant_id == workspace_id,
            )
        )
        if row is None:
            raise LookupError("Software project not found")
        return row

    async def get(self, db, workspace_id: str, project_id: str) -> SoftwareProject:
        return await self._as_project(db, await self.record(db, workspace_id, project_id))

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
