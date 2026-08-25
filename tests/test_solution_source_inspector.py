import json
import unittest
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.api.solution_generation_router import _latest_generated_source, _source_inspector_json
from packages.database.custom_software_models import GeneratedProject, GeneratedSourceBundle, SoftwarePlanRecord
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.product_models import SolutionRecord
from packages.database.schema import import_all_models
from packages.solutions.service import LifecycleStatus, RuntimeType, SolutionType


class SolutionSourceInspectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.user = AppUser(email="source-inspector@operly.test", password_hash="x")
        self.tenant = Tenant(name="Source Inspector")
        self.other_tenant = Tenant(name="Other Tenant")
        self.db.add_all([self.user, self.tenant, self.other_tenant])
        await self.db.flush()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def test_latest_repaired_source_is_exposed_as_read_only_file_content(self):
        plan = SoftwarePlanRecord(
            tenant_id=self.tenant.id,
            prompt="Build an employee clock in app",
            current_version=1,
            approved_version=1,
            status="approved",
            created_by=self.user.id,
        )
        self.db.add(plan)
        await self.db.flush()

        project = GeneratedProject(
            tenant_id=self.tenant.id,
            slug="clock-in",
            name="Clock in",
            vertical="custom",
            prompt=plan.prompt,
            brand_json="{}",
            artifact_graph_json="{}",
            plan_id=plan.id,
            approved_plan_version=1,
            architecture_pack="custom",
            created_by=self.user.id,
        )
        self.db.add(project)
        await self.db.flush()

        solution = SolutionRecord(
            tenant_id=self.tenant.id,
            name="Employee clock in",
            description="Scan QR codes to clock in and out",
            solution_type=SolutionType.CUSTOM_SOLUTION,
            lifecycle_status=LifecycleStatus.FAILED,
            runtime_type=RuntimeType.GENERATED_PROJECT,
            runtime_reference=project.id,
        )
        first = GeneratedSourceBundle(
            tenant_id=self.tenant.id,
            plan_id=plan.id,
            plan_version=1,
            source_version=1,
            application_id=f"plan-{plan.id}",
            bundle_digest="sha256:first",
            manifest_json=json.dumps({"files": [{"path": "backend/app.py"}], "totalBytes": 4}),
            files_json=json.dumps([{"path": "backend/app.py", "content": "old\n", "generatedBy": "coding_harness"}]),
            provenance_json=json.dumps({"summary": "Initial source", "sourceOperation": "generate"}),
            created_by=self.user.id,
        )
        repaired = GeneratedSourceBundle(
            tenant_id=self.tenant.id,
            plan_id=plan.id,
            plan_version=1,
            source_version=2,
            application_id=f"plan-{plan.id}",
            bundle_digest="sha256:repaired",
            manifest_json=json.dumps({"files": [{"path": "backend/app.py"}, {"path": "operly.solution.json"}], "totalBytes": 28}),
            files_json=json.dumps([
                {"path": "operly.solution.json", "content": "{}\n", "generatedBy": "coding_harness"},
                {"path": "backend/app.py", "content": "print('healthy')\n", "generatedBy": "coding_harness"},
            ]),
            provenance_json=json.dumps({"summary": "Health repair", "sourceOperation": "runner_repair"}),
            created_by=self.user.id,
        )
        self.db.add_all([solution, first, repaired])
        await self.db.commit()

        latest = await _latest_generated_source(self.db, self.tenant.id, solution)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.id, repaired.id)

        payload = _source_inspector_json(latest)
        self.assertEqual(payload["sourceVersion"], 2)
        self.assertEqual(payload["fileCount"], 2)
        self.assertEqual([item["path"] for item in payload["files"]], ["backend/app.py", "operly.solution.json"])
        self.assertEqual(payload["files"][0]["content"], "print('healthy')\n")
        self.assertEqual(payload["sourceOperation"], "runner_repair")

        isolated = await _latest_generated_source(self.db, self.other_tenant.id, solution)
        self.assertIsNone(isolated)

    def test_solutions_ui_has_clickable_generated_file_inspector(self):
        frontend = Path("apps/web/src/workspace/SolutionsPage.tsx").read_text(encoding="utf-8")
        self.assertIn("View generated files", frontend)
        self.assertIn("/source`", frontend)
        self.assertIn('role="dialog"', frontend)
        self.assertIn("selectedSourceFile?.content", frontend)


if __name__ == "__main__":
    unittest.main()
