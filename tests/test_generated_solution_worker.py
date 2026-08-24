import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.database.custom_software_models import GeneratedProject
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.product_models import SolutionJob
from packages.database.schema import import_all_models
from packages.solutions.composer import create_solution_from_intent, retry_solution_initial_generation
from packages.solutions.generation_worker import (
    GENERATED_JOB_TYPE,
    claim_next_generation_job,
    process_generation_job,
)
from packages.solutions.service import LifecycleStatus, SolutionService, solution_json


class GeneratedSolutionWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.factory()
        self.user = AppUser(email="owner@generated-worker.test", password_hash="x")
        self.tenant = Tenant(name="Generated Worker")
        self.db.add_all([self.user, self.tenant])
        await self.db.flush()
        self.service = SolutionService()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _create_generated(self):
        row, decision = await create_solution_from_intent(
            self.db,
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            name="Employee Clock in and Clock out system",
            objective="Employees should be able to clock in using their cameras, by scanning a QR code and clocking out by using another QR code.",
            service=self.service,
        )
        await self.db.commit()
        return row, decision

    async def _jobs(self, solution_id):
        return (
            await self.db.scalars(
                select(SolutionJob)
                .where(SolutionJob.solution_id == solution_id)
                .order_by(SolutionJob.attempt)
            )
        ).all()

    async def test_compose_returns_queued_before_planning_or_source_generation(self):
        row, decision = await self._create_generated()

        self.assertEqual(decision.runtime_type, "generated_project")
        self.assertEqual(row.lifecycle_status, LifecycleStatus.BUILDING)
        self.assertEqual(row.preview_state, "unavailable")
        payload = solution_json(row)
        self.assertEqual(payload["generation"]["status"], "queued")
        self.assertEqual(payload["generation"]["stage"], "planning")

        jobs = await self._jobs(row.id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_type, GENERATED_JOB_TYPE)
        self.assertEqual(jobs[0].status, "queued")
        self.assertEqual(jobs[0].created_by, self.user.id)
        self.assertIsNone(jobs[0].plan_id)
        project = await self.db.get(GeneratedProject, row.runtime_reference)
        self.assertIsNotNone(project)
        self.assertIsNone(project.plan_id)

        synced = await self.service.get(self.db, self.tenant.id, row.id)
        self.assertEqual(synced.lifecycle_status, LifecycleStatus.BUILDING)
        self.assertEqual(synced.preview_state, "unavailable")

    async def test_expired_worker_lease_is_reclaimed(self):
        row, _ = await self._create_generated()
        job = (await self._jobs(row.id))[0]
        job.status = "running"
        job.locked_by = "dead-worker"
        job.heartbeat_at = datetime.utcnow() - timedelta(minutes=10)
        job.lease_expires_at = datetime.utcnow() - timedelta(minutes=5)
        await self.db.commit()

        with patch("packages.solutions.generation_worker.SessionFactory", self.factory):
            claimed = await claim_next_generation_job("replacement-worker")

        self.assertEqual(claimed, job.id)
        await self.db.refresh(job)
        self.assertEqual(job.status, "running")
        self.assertEqual(job.locked_by, "replacement-worker")
        self.assertGreater(job.lease_expires_at, datetime.utcnow())
        logs = json.loads(job.log_json)
        self.assertEqual(logs[-1]["status"], "reclaimed")

    async def test_failed_generated_solution_retry_only_queues_new_attempt(self):
        row, _ = await self._create_generated()
        first = (await self._jobs(row.id))[0]
        first.status = "failed"
        first.ended_at = datetime.utcnow()
        context = json.loads(row.context_json)
        context["initialGeneration"] = {
            "status": "retryable",
            "stage": "planning",
            "jobId": first.id,
            "attempt": 1,
            "error": "planner unavailable",
        }
        row.context_json = json.dumps(context)
        row.lifecycle_status = LifecycleStatus.FAILED
        await self.db.commit()

        retried = await retry_solution_initial_generation(
            self.db,
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            solution_id=row.id,
            service=self.service,
        )
        await self.db.commit()

        self.assertEqual(retried.lifecycle_status, LifecycleStatus.BUILDING)
        jobs = await self._jobs(row.id)
        self.assertEqual([item.attempt for item in jobs], [1, 2])
        self.assertEqual([item.status for item in jobs], ["failed", "queued"])

    async def test_worker_marks_preview_ready_only_after_verified_build(self):
        row, _ = await self._create_generated()
        job = (await self._jobs(row.id))[0]
        job.status = "running"
        job.locked_by = "worker-a"
        job.started_at = datetime.utcnow()
        job.lease_expires_at = datetime.utcnow() + timedelta(minutes=2)
        await self.db.commit()

        plan_row = SimpleNamespace(id="plan-1", approved_version=1, status="approved")
        plan = SimpleNamespace()
        source = SimpleNamespace(id="source-1", source_version=3)
        build = SimpleNamespace(id="build-1", state="preview_ready", failure_classification=None)
        with patch(
            "packages.solutions.generation_worker._ensure_plan",
            new=AsyncMock(return_value=(plan_row, plan)),
        ), patch(
            "packages.solutions.generation_worker._bind_project",
            new=AsyncMock(),
        ), patch(
            "packages.solutions.generation_worker.build_with_repair",
            new=AsyncMock(return_value=(build, source, [])),
        ):
            await process_generation_job(self.db, job)

        await self.db.refresh(row)
        await self.db.refresh(job)
        self.assertEqual(row.lifecycle_status, LifecycleStatus.PREVIEW_READY)
        self.assertEqual(row.preview_state, "ready")
        self.assertEqual(row.current_version_reference, "3")
        self.assertEqual(job.status, "succeeded")
        self.assertIsNone(job.locked_by)
        self.assertIsNone(job.lease_expires_at)


if __name__ == "__main__":
    unittest.main()
