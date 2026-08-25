import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.database.db import Base
from packages.database.models import Tenant
from packages.database.product_models import SolutionRecord
from packages.database.schema import import_all_models
from packages.database.software_project_models import SoftwareProjectRecord
from packages.solutions.service import LifecycleStatus, RuntimeType, SolutionService, SolutionType


class SolutionLibraryCanonicalIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_canonical_software_solution_hides_matching_legacy_adapter_from_library_only(self):
        async with self.sessions() as db:
            tenant = Tenant(name="Canonical Library", slug="canonical-library")
            db.add(tenant)
            await db.flush()

            project = SoftwareProjectRecord(
                tenant_id=tenant.id,
                name="Migrated Studio Site",
                description="One project, one visible Solution identity.",
                legacy_runtime_type=RuntimeType.STUDIO,
                legacy_runtime_reference="legacy-studio-project",
            )
            db.add(project)
            await db.flush()

            legacy = SolutionRecord(
                tenant_id=tenant.id,
                name="Migrated Studio Site",
                description="Legacy publication adapter",
                solution_type=SolutionType.DIGITAL_PRESENCE,
                lifecycle_status=LifecycleStatus.PREVIEW_READY,
                runtime_type=RuntimeType.STUDIO,
                runtime_reference="legacy-studio-project",
                preview_state="ready",
            )
            canonical = SolutionRecord(
                tenant_id=tenant.id,
                name="Migrated Studio Site",
                description="Canonical software identity",
                solution_type=SolutionType.CUSTOM_SOLUTION,
                lifecycle_status=LifecycleStatus.DRAFT,
                runtime_type=RuntimeType.SOFTWARE_PROJECT,
                runtime_reference=project.id,
            )
            db.add_all([legacy, canonical])
            await db.commit()

            service = SolutionService()
            rows = await service.list(db, tenant.id)

            self.assertEqual([row.id for row in rows], [canonical.id])
            self.assertEqual(rows[0].runtime_type, RuntimeType.SOFTWARE_PROJECT)

            # The adapter remains directly addressable until canonical publication
            # fully replaces the legacy Studio deployment path.
            resolved_legacy = await service.get(db, tenant.id, legacy.id)
            self.assertEqual(resolved_legacy.id, legacy.id)
            self.assertEqual(resolved_legacy.runtime_type, RuntimeType.STUDIO)


if __name__ == "__main__":
    unittest.main()
