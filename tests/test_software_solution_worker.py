import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.database.custom_software_models import SoftwarePlanRecord
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.product_models import SolutionJob
from packages.database.schema import import_all_models
from packages.database.software_project_models import SoftwareProjectRecord
from packages.software_projects.planning.compiler_planning import PLANNING_ENGINE_VERSION as PLANNING_ENGINE
from packages.solutions.composer import create_solution_from_intent, retry_solution_initial_generation
from packages.solutions.generation_worker import (
    SOFTWARE_JOB_TYPE,
    claim_next_generation_job,
)
from packages.solutions.service import LifecycleStatus, RuntimeType, SolutionService, SolutionType, solution_json


class SoftwareSolutionWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.factory()
        self.user = AppUser(email="owner@software-worker.test", password_hash="x")
        self.tenant = Tenant(name="Software Worker")
        self.db.add_all([self.user, self.tenant])
        await self.db.flush()
        self.service = SolutionService()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _create_software(self):
        row, decision = await create_solution_from_intent(
            self.db,
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            name="Employee Clock in and Clock out system",
            objective=(
                "Employees should be able to clock in using their cameras, by scanning "
                "a QR code and clocking out by using another QR code."
            ),
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

    async def _fail_job(self, row, job, *, stage="source_generation", error="build failed"):
        job.status = "failed"
        job.ended_at = datetime.utcnow()
        context = json.loads(row.context_json)
        context["initialGeneration"] = {
            "status": "retryable",
            "stage": stage,
            "jobId": job.id,
            "attempt": job.attempt,
            "error": error,
        }
        row.context_json = json.dumps(context)
        row.lifecycle_status = LifecycleStatus.FAILED
        await self.db.commit()

    async def test_compose_creates_only_software_project_and_queues_software_job(self):
        row, decision = await self._create_software()

        self.assertEqual(decision.runtime_type, RuntimeType.SOFTWARE_PROJECT)
        self.assertEqual(decision.solution_type, SolutionType.CUSTOM_SOLUTION)
        self.assertEqual(decision.implementation_mode, "software_project")
        self.assertEqual(row.runtime_type, RuntimeType.SOFTWARE_PROJECT)
        self.assertEqual(row.lifecycle_status, LifecycleStatus.BUILDING)
        self.assertEqual(row.preview_state, "unavailable")

        project = await self.db.get(SoftwareProjectRecord, row.runtime_reference)
        self.assertIsNotNone(project)
        self.assertEqual(project.tenant_id, self.tenant.id)

        jobs = await self._jobs(row.id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_type, SOFTWARE_JOB_TYPE)
        self.assertEqual(jobs[0].status, "queued")
        self.assertEqual(jobs[0].created_by, self.user.id)
        self.assertIsNone(jobs[0].plan_id)
        evidence = json.loads(jobs[0].evidence_json)
        self.assertEqual(evidence["planningEngine"], PLANNING_ENGINE)
        self.assertEqual(len(evidence["planningInputDigest"]), 64)

        payload = solution_json(row)
        self.assertEqual(payload["runtime"]["kind"], RuntimeType.SOFTWARE_PROJECT)
        self.assertEqual(payload["generation"]["status"], "queued")
        self.assertEqual(payload["generation"]["stage"], "planning")

    async def test_expired_worker_lease_is_reclaimed(self):
        row, _ = await self._create_software()
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
        self.assertEqual(json.loads(job.log_json)[-1]["status"], "reclaimed")

    async def test_retry_reuses_matching_approved_plan(self):
        row, _ = await self._create_software()
        first = (await self._jobs(row.id))[0]
        plan = SoftwarePlanRecord(
            tenant_id=self.tenant.id,
            prompt="approved fixture plan",
            current_version=1,
            approved_version=1,
            status="approved",
            created_by=self.user.id,
        )
        self.db.add(plan)
        await self.db.flush()
        first.plan_id = plan.id
        evidence = json.loads(first.evidence_json)
        evidence.update({"softwarePlanId": plan.id, "softwarePlanVersion": 1})
        first.evidence_json = json.dumps(evidence)
        await self._fail_job(row, first)

        retried = await retry_solution_initial_generation(
            self.db,
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            solution_id=row.id,
            service=self.service,
        )
        await self.db.commit()

        second = (await self._jobs(row.id))[-1]
        self.assertEqual(second.attempt, 2)
        self.assertEqual(second.job_type, SOFTWARE_JOB_TYPE)
        self.assertEqual(second.plan_id, plan.id)
        self.assertEqual(second.source_version_reference, f"software-plan:{plan.id}:1")
        retry_evidence = json.loads(second.evidence_json)
        self.assertEqual(retry_evidence["reusedSoftwarePlanId"], plan.id)
        self.assertEqual(retry_evidence["reusedSoftwarePlanVersion"], 1)
        self.assertEqual(solution_json(retried)["generation"]["stage"], "source_generation")

    async def test_retry_replans_when_owner_intent_changes(self):
        row, _ = await self._create_software()
        first = (await self._jobs(row.id))[0]
        plan = SoftwarePlanRecord(
            tenant_id=self.tenant.id,
            prompt="approved fixture plan",
            current_version=1,
            approved_version=1,
            status="approved",
            created_by=self.user.id,
        )
        self.db.add(plan)
        await self.db.flush()
        first.plan_id = plan.id
        await self._fail_job(row, first)

        context = json.loads(row.context_json)
        context["ownerIntent"]["objective"] = (
            "Build a changed attendance system with manager approval."
        )
        row.context_json = json.dumps(context)
        await self.db.commit()

        retried = await retry_solution_initial_generation(
            self.db,
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            solution_id=row.id,
            service=self.service,
        )
        await self.db.commit()

        second = (await self._jobs(row.id))[-1]
        self.assertIsNone(second.plan_id)
        self.assertEqual(second.job_type, SOFTWARE_JOB_TYPE)
        self.assertEqual(solution_json(retried)["generation"]["stage"], "planning")
        self.assertNotEqual(
            json.loads(second.evidence_json)["planningInputDigest"],
            json.loads(first.evidence_json)["planningInputDigest"],
        )


if __name__ == "__main__":
    unittest.main()
