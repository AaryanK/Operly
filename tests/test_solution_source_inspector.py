import unittest
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.api.solution_generation_router import _canonical_source_inspector_json
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.schema import import_all_models
from packages.software_projects import SoftwareProjectService, SoftwareSourceService
from packages.solutions.service import SolutionService


class SolutionSourceInspectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.user = AppUser(email="source-inspector@operly.test", password_hash="x")
        self.tenant = Tenant(name="Source Inspector")
        self.other_tenant = Tenant(name="Other Tenant")
        self.db.add_all([self.user, self.tenant, self.other_tenant])
        await self.db.flush()
        self.projects = SoftwareProjectService()
        self.sources = SoftwareSourceService()
        self.solutions = SolutionService()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def test_latest_canonical_source_is_exposed_as_read_only_file_content(self):
        project = await self.projects.create(
            self.db,
            workspace_id=self.tenant.id,
            user_id=self.user.id,
            name="Employee clock in",
            description="Scan QR codes to clock in and out",
        )
        first = await self.sources.persist(
            self.db,
            tenant_id=self.tenant.id,
            project_id=project.id,
            user_id=self.user.id,
            files={"backend/app.py": "old\n"},
            runtime_profile="python",
            provenance={"sourceOperation": "generate"},
            change_summary="Initial source",
        )
        repaired = await self.sources.persist(
            self.db,
            tenant_id=self.tenant.id,
            project_id=project.id,
            user_id=self.user.id,
            files={
                "operly.solution.json": "{}\n",
                "backend/app.py": "print('healthy')\n",
            },
            runtime_profile="python",
            provenance={"sourceOperation": "runner_repair"},
            change_summary="Health repair",
            parent_source_id=first.id,
        )
        record = await self.projects.record(self.db, self.tenant.id, project.id)
        await self.solutions.create_software_solution(
            self.db,
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            project=record,
            objective=record.description,
        )
        await self.db.commit()

        latest = await self.sources.latest(self.db, self.tenant.id, project.id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.id, repaired.id)

        payload = _canonical_source_inspector_json(latest)
        self.assertEqual(payload["sourceVersion"], 2)
        self.assertEqual(payload["fileCount"], 2)
        self.assertEqual(
            [item["path"] for item in payload["files"]],
            ["backend/app.py", "operly.solution.json"],
        )
        self.assertEqual(payload["files"][0]["content"], "print('healthy')\n")
        self.assertEqual(payload["sourceAuthority"], "software_source_versions")
        self.assertEqual(payload["summary"], "Health repair")

        isolated = await self.sources.latest(
            self.db, self.other_tenant.id, project.id
        )
        self.assertIsNone(isolated)

    def test_solutions_ui_has_clickable_source_file_inspector(self):
        frontend = Path("apps/web/src/workspace/SolutionsPage.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("View generated files", frontend)
        self.assertIn("/source`", frontend)
        self.assertIn('role="dialog"', frontend)
        self.assertIn("selectedSourceFile?.content", frontend)


if __name__ == "__main__":
    unittest.main()
