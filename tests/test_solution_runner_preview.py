from datetime import datetime, timedelta
import unittest

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


class SolutionRunnerPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.user = AppUser(email="runner-preview@solution.test", password_hash="x")
        self.tenant = Tenant(name="Runner Preview")
        self.db.add_all([self.user, self.tenant])
        await self.db.flush()
        self.service = SolutionService()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _generated_solution_with_preview(self, expires_at):
        plan = SoftwarePlanRecord(
            tenant_id=self.tenant.id,
            prompt="Build a full-stack scheduling app",
            current_version=1,
            approved_version=1,
            status="approved",
            created_by=self.user.id,
        )
        self.db.add(plan)
        await self.db.flush()

        project = GeneratedProject(
            tenant_id=self.tenant.id,
            slug="runner-backed-app",
            name="Runner Backed App",
            vertical="general",
            prompt=plan.prompt,
            brand_json="{}",
            artifact_graph_json="{}",
            plan_id=plan.id,
            approved_plan_version=1,
            architecture_pack="general",
            created_by=self.user.id,
        )
        source = GeneratedSourceBundle(
            tenant_id=self.tenant.id,
            plan_id=plan.id,
            plan_version=1,
            source_version=1,
            application_id=f"plan-{plan.id}",
            bundle_digest="sha256:test-runner-preview",
            manifest_json="{}",
            files_json="[]",
            provenance_json="{}",
            created_by=self.user.id,
        )
        self.db.add_all([project, source])
        await self.db.flush()

        build = RunnerBuildRecord(
            tenant_id=self.tenant.id,
            plan_id=plan.id,
            source_bundle_id=source.id,
            runner_job_id="runner-job-1",
            idempotency_key="runner-preview-build-1",
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
            runner_preview_id="runner-preview-1",
            state="active",
            target_url="https://runner-preview.example",
            expires_at=expires_at,
            created_by=self.user.id,
        )
        self.db.add(preview)
        await self.db.commit()
        rows = await self.service.list(self.db, self.tenant.id)
        solution = next(
            row
            for row in rows
            if row.runtime_type == RuntimeType.GENERATED_PROJECT
            and row.runtime_reference == project.id
        )
        return solution, project, preview

    async def test_generated_solution_uses_active_isolated_runner_preview(self):
        solution, project, preview = await self._generated_solution_with_preview(
            datetime.utcnow() + timedelta(minutes=15)
        )

        self.assertEqual(solution.lifecycle_status, LifecycleStatus.PREVIEW_READY)
        self.assertEqual(solution.preview_state, "ready")
        self.assertEqual(
            await self.service.preview_target(
                self.db,
                self.tenant.id,
                solution,
                project,
            ),
            f"/api/custom-software/previews/{preview.id}/",
        )

    async def test_expired_runner_preview_falls_back_to_legacy_project_preview(self):
        solution, project, _ = await self._generated_solution_with_preview(
            datetime.utcnow() - timedelta(minutes=1)
        )

        self.assertEqual(solution.lifecycle_status, LifecycleStatus.APPROVED)
        self.assertEqual(solution.preview_state, "available")
        self.assertEqual(
            await self.service.preview_target(
                self.db,
                self.tenant.id,
                solution,
                project,
            ),
            f"/api/custom-software/projects/{project.id}/preview",
        )
