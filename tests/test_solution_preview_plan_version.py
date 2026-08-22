import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.database.custom_software_models import (
    GeneratedProject,
    GeneratedSourceBundle,
    RunnerBuildRecord,
    RunnerPreviewRecord,
    SoftwarePlanRecord,
)
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.schema import import_all_models
from packages.solutions import LifecycleStatus, RuntimeType, SolutionService


class GeneratedPreviewPlanVersionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.user = AppUser(email="owner@preview-version.test", password_hash="x")
        self.tenant = Tenant(name="Preview Version Tenant")
        self.db.add_all([self.user, self.tenant])
        await self.db.flush()
        self.service = SolutionService()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _add_preview(self, plan, plan_version, source_version, suffix):
        source = GeneratedSourceBundle(
            tenant_id=self.tenant.id,
            plan_id=plan.id,
            plan_version=plan_version,
            source_version=source_version,
            application_id=f"plan-{plan.id}",
            bundle_digest=f"sha256:{suffix}",
            manifest_json="{}",
            files_json="[]",
            provenance_json=json.dumps({"summary": suffix}),
            created_by=self.user.id,
        )
        self.db.add(source)
        await self.db.flush()
        build = RunnerBuildRecord(
            tenant_id=self.tenant.id,
            plan_id=plan.id,
            source_bundle_id=source.id,
            runner_job_id=f"runner-{suffix}",
            idempotency_key=f"build-{suffix}",
            state="preview_ready",
            runner_implementation="test-runner",
            isolation_profile="isolated-test",
            submission_json="{}",
            result_json="{}",
            created_by=self.user.id,
        )
        self.db.add(build)
        await self.db.flush()
        preview = RunnerPreviewRecord(
            tenant_id=self.tenant.id,
            build_id=build.id,
            runner_preview_id=f"preview-{suffix}",
            state="active",
            target_url=f"https://{suffix}.example",
            expires_at=datetime.utcnow() + timedelta(minutes=10),
            created_by=self.user.id,
        )
        self.db.add(preview)
        await self.db.flush()
        return preview

    async def test_solution_preview_ignores_old_approved_plan_version(self):
        plan = SoftwarePlanRecord(
            tenant_id=self.tenant.id,
            prompt="Build a revisioned full-stack app",
            current_version=2,
            approved_version=2,
            status="approved",
            created_by=self.user.id,
        )
        self.db.add(plan)
        await self.db.flush()
        project = GeneratedProject(
            tenant_id=self.tenant.id,
            slug="preview-version-scoped",
            name="Preview Version Scoped",
            vertical="general",
            prompt=plan.prompt,
            brand_json="{}",
            artifact_graph_json="{}",
            plan_id=plan.id,
            approved_plan_version=2,
            architecture_pack="general",
            created_by=self.user.id,
        )
        self.db.add(project)
        old_preview = await self._add_preview(plan, 1, 1, "old-plan")
        await self.db.commit()

        rows = await self.service.list(self.db, self.tenant.id)
        solution = next(
            row
            for row in rows
            if row.runtime_type == RuntimeType.GENERATED_PROJECT and row.runtime_reference == project.id
        )
        self.assertEqual(solution.lifecycle_status, LifecycleStatus.APPROVED)
        self.assertEqual(solution.preview_state, "available")
        self.assertNotEqual(
            await self.service.preview_target(self.db, self.tenant.id, solution, project),
            f"/api/custom-software/previews/{old_preview.id}/",
        )
        self.assertEqual(
            await self.service.preview_target(self.db, self.tenant.id, solution, project),
            f"/api/custom-software/projects/{project.id}/preview",
        )

        current_preview = await self._add_preview(plan, 2, 2, "current-plan")
        await self.db.commit()
        solution = await self.service.get(self.db, self.tenant.id, solution.id)
        self.assertEqual(solution.lifecycle_status, LifecycleStatus.PREVIEW_READY)
        self.assertEqual(solution.preview_state, "ready")
        self.assertEqual(
            await self.service.preview_target(self.db, self.tenant.id, solution, project),
            f"/api/custom-software/previews/{current_preview.id}/",
        )
