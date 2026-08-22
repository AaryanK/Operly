"""Canonical read facade over legacy/current software project records."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from packages.database.application_builder_models import ManagedApplication
from packages.database.custom_software_models import GeneratedProject
from packages.database.studio_models import StudioProject
from packages.software_projects.adapters import (
    from_generated_project,
    from_managed_application,
    from_studio_project,
)


class SoftwareProjectService:
    """Compatibility service while canonical SoftwareProject persistence lands.

    New Studio orchestration can consume one project shape now instead of branching
    on three product generations. Writes remain owned by existing services until
    the persistence migration is complete.
    """

    async def list(self, db, workspace_id: str):
        studios = (
            await db.scalars(
                select(StudioProject).where(
                    StudioProject.tenant_id == workspace_id,
                    StudioProject.status != "archived",
                )
            )
        ).all()
        apps = (
            await db.scalars(
                select(ManagedApplication).where(
                    ManagedApplication.tenant_id == workspace_id
                )
            )
        ).all()
        generated = (
            await db.scalars(
                select(GeneratedProject).where(
                    GeneratedProject.tenant_id == workspace_id
                )
            )
        ).all()
        projects = [*(from_studio_project(row) for row in studios)]
        projects.extend(from_managed_application(row) for row in apps)
        projects.extend(from_generated_project(row) for row in generated)
        projects.sort(
            key=lambda item: item.updated_at or item.created_at or datetime.min,
            reverse=True,
        )
        return projects

    async def get(self, db, workspace_id: str, project_id: str):
        for model, adapter in (
            (StudioProject, from_studio_project),
            (ManagedApplication, from_managed_application),
            (GeneratedProject, from_generated_project),
        ):
            row = await db.scalar(
                select(model).where(
                    model.id == project_id,
                    model.tenant_id == workspace_id,
                )
            )
            if row is not None:
                return adapter(row)
        raise LookupError("Software project not found")
