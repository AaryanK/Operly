import json
import unittest
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.product_models import SolutionJob
from packages.database.schema import import_all_models
from packages.database.software_project_models import SoftwareProjectRecord
from packages.solutions.composer import (
    create_solution_from_intent,
    retry_solution_initial_generation,
)
from packages.solutions.generation_worker import SOFTWARE_JOB_TYPE
from packages.solutions.service import LifecycleStatus, RuntimeType, SolutionService, solution_json


class SolutionInitialGenerationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.user = AppUser(email="owner@generation.test", password_hash="x")
        self.tenant = Tenant(name="Generation Test")
        self.db.add_all([self.user, self.tenant])
        await self.db.flush()
        self.service = SolutionService()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _create(self):
        row, decision = await create_solution_from_intent(
            self.db,
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            name="Customer Notebook",
            objective="Build a lightweight customer notebook.",
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

    async def test_initial_generation_is_durable_software_project_work(self):
        row, decision = await self._create()

        self.assertEqual(decision.runtime_type, RuntimeType.SOFTWARE_PROJECT)
        self.assertEqual(row.runtime_type, RuntimeType.SOFTWARE_PROJECT)
        self.assertEqual(row.lifecycle_status, LifecycleStatus.BUILDING)
        self.assertEqual(row.preview_state, "unavailable")
        self.assertIsNone(row.current_version_reference)

        project = await self.db.get(SoftwareProjectRecord, row.runtime_reference)
        self.assertIsNotNone(project)
        self.assertEqual(project.state, "building")
        self.assertIsNone(project.active_source_version_id)

        jobs = await self._jobs(row.id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_type, SOFTWARE_JOB_TYPE)
        self.assertEqual(jobs[0].status, "queued")
        self.assertEqual(jobs[0].attempt, 1)

        payload = solution_json(row)
        self.assertEqual(payload["runtime"], {"kind": "software", "id": project.id})
        self.assertEqual(payload["generation"]["status"], "queued")
        self.assertEqual(payload["generation"]["stage"], "planning")

    async def test_retry_while_generation_is_active_does_not_duplicate_work(self):
        row, _ = await self._create()
        first = (await self._jobs(row.id))[0]

        retried = await retry_solution_initial_generation(
            self.db,
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            solution_id=row.id,
            service=self.service,
        )
        await self.db.commit()

        jobs = await self._jobs(row.id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].id, first.id)
        self.assertEqual(retried.lifecycle_status, LifecycleStatus.BUILDING)

    async def test_failed_generation_retries_from_stored_owner_objective(self):
        row, _ = await self._create()
        first = (await self._jobs(row.id))[0]
        first.status = "failed"
        first.ended_at = datetime.utcnow()
        row.lifecycle_status = LifecycleStatus.FAILED
        context = json.loads(row.context_json)
        context["initialGeneration"] = {
            "status": "retryable",
            "stage": "source_generation",
            "jobId": first.id,
            "attempt": 1,
            "error": "first build failed",
        }
        row.context_json = json.dumps(context)
        project = await self.db.get(SoftwareProjectRecord, row.runtime_reference)
        project.state = "failed"
        await self.db.commit()

        retried = await retry_solution_initial_generation(
            self.db,
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            solution_id=row.id,
            service=self.service,
        )
        await self.db.commit()

        jobs = await self._jobs(row.id)
        self.assertEqual([job.attempt for job in jobs], [1, 2])
        self.assertEqual([job.status for job in jobs], ["failed", "queued"])
        evidence = json.loads(jobs[1].evidence_json)
        self.assertEqual(evidence["objective"], "Build a lightweight customer notebook.")
        self.assertEqual(retried.lifecycle_status, LifecycleStatus.BUILDING)
        self.assertEqual(solution_json(retried)["generation"]["attempt"], 2)


if __name__ == "__main__":
    unittest.main()
