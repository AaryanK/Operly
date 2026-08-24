import json
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.database.custom_software_models import (
    GeneratedProject,
    GeneratedSourceBundle,
    SoftwarePlanRecord,
)
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.product_models import SolutionJob, SolutionRecord
from packages.database.schema import import_all_models
from packages.solutions.composer import create_solution_from_intent
from packages.solutions.generation_worker import _create_plan_record
from packages.solutions.service import (
    LifecycleStatus,
    RuntimeType,
    SolutionService,
    SolutionType,
    solution_json,
)


class GeneratedSolutionLifecycleTruthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.user = AppUser(email="owner@generated-truth.test", password_hash="x")
        self.tenant = Tenant(name="Generated Truth")
        self.db.add_all([self.user, self.tenant])
        await self.db.flush()
        self.service = SolutionService()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _fixture(self, generation_status: str):
        plan = SoftwarePlanRecord(
            tenant_id=self.tenant.id,
            prompt="Build a camera QR attendance application",
            current_version=1,
            approved_version=1,
            status="approved",
            created_by=self.user.id,
        )
        self.db.add(plan)
        await self.db.flush()
        project = GeneratedProject(
            tenant_id=self.tenant.id,
            slug=f"camera-attendance-{generation_status}",
            name="Camera Attendance",
            vertical="custom",
            prompt=plan.prompt,
            brand_json="{}",
            artifact_graph_json="{}",
            plan_id=plan.id,
            approved_plan_version=1,
            architecture_pack="custom",
            created_by=self.user.id,
        )
        source = GeneratedSourceBundle(
            tenant_id=self.tenant.id,
            plan_id=plan.id,
            plan_version=1,
            source_version=1,
            application_id=f"plan-{plan.id}",
            bundle_digest=f"sha256:{generation_status}",
            manifest_json="{}",
            files_json="[]",
            provenance_json=json.dumps({"summary": "Generated source exists"}),
            created_by=self.user.id,
        )
        self.db.add_all([project, source])
        await self.db.flush()
        solution = SolutionRecord(
            tenant_id=self.tenant.id,
            name="Camera Attendance",
            description=plan.prompt,
            solution_type=SolutionType.CUSTOM_SOLUTION,
            lifecycle_status=(
                LifecycleStatus.FAILED
                if generation_status == "retryable"
                else LifecycleStatus.PREVIEW_READY
            ),
            current_version_reference=None,
            preview_state="unavailable",
            preview_url=None,
            production_state="offline",
            production_url=None,
            visibility="private",
            runtime_type=RuntimeType.GENERATED_PROJECT,
            runtime_reference=project.id,
            context_json=json.dumps(
                {
                    "initialGeneration": {
                        "status": generation_status,
                        "stage": "acceptance_test",
                        "jobId": "job-1",
                        "attempt": 1,
                        "softwarePlanId": plan.id,
                        "softwarePlanVersion": 1,
                        "sourceBundleId": source.id,
                        "sourceVersion": 1,
                        "buildId": "build-1",
                    }
                }
            ),
        )
        self.db.add(solution)
        await self.db.commit()
        return solution, project

    async def test_generated_job_persists_plan_reference_before_source_exists(self):
        solution, _ = await create_solution_from_intent(
            self.db,
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            name="Employee Clock in and Clock out system",
            objective="Employees scan QR codes with their cameras to clock in and out.",
            service=self.service,
        )
        await self.db.commit()
        job = await self.db.scalar(
            select(SolutionJob).where(SolutionJob.solution_id == solution.id)
        )
        self.assertIsNotNone(job)
        self.assertIsNone(job.plan_id)

        plan = await _create_plan_record(self.db, job, solution, self.user.id)

        self.assertEqual(job.plan_id, plan.id)
        self.assertEqual(job.source_version_reference, f"software-plan:{plan.id}:pending")
        self.assertEqual(job.status, "queued")
        self.assertEqual(solution.lifecycle_status, LifecycleStatus.BUILDING)
        self.assertEqual(solution.preview_state, "unavailable")

    async def test_failed_build_is_not_resurrected_by_solution_sync(self):
        solution, project = await self._fixture("retryable")

        synced = await self.service.get(self.db, self.tenant.id, solution.id)

        self.assertEqual(synced.lifecycle_status, LifecycleStatus.FAILED)
        self.assertEqual(synced.preview_state, "unavailable")
        self.assertIsNone(synced.preview_url)
        self.assertIsNone(synced.current_version_reference)
        payload = solution_json(synced)
        self.assertEqual(payload["generation"]["sourceVersion"], 1)
        self.assertEqual(payload["generation"]["buildId"], "build-1")
        with self.assertRaisesRegex(LookupError, "not ready"):
            await self.service.preview_target(self.db, self.tenant.id, synced, project)

    async def test_expired_verified_runner_preview_does_not_fall_back_to_mock_renderer(self):
        solution, project = await self._fixture("applied")

        synced = await self.service.get(self.db, self.tenant.id, solution.id)

        self.assertEqual(synced.lifecycle_status, LifecycleStatus.APPROVED)
        self.assertEqual(synced.current_version_reference, "1")
        self.assertEqual(synced.preview_state, "unavailable")
        self.assertIsNone(synced.preview_url)
        with self.assertRaisesRegex(LookupError, "not ready"):
            await self.service.preview_target(self.db, self.tenant.id, synced, project)


if __name__ == "__main__":
    unittest.main()
