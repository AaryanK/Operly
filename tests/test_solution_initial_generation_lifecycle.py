import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.application_builder.service import ApplicationBuilderService
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.product_models import SolutionJob
from packages.database.schema import import_all_models
from packages.solutions.composer import (
    create_solution_from_intent,
    retry_solution_initial_generation,
)
from packages.solutions.service import LifecycleStatus, SolutionService, solution_json


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

    async def _jobs(self, solution_id):
        return (
            await self.db.scalars(
                select(SolutionJob)
                .where(SolutionJob.solution_id == solution_id)
                .order_by(SolutionJob.attempt)
            )
        ).all()

    async def test_failed_initial_generation_never_marks_bootstrap_preview_ready(self):
        with patch.object(
            ApplicationBuilderService,
            "propose",
            new=AsyncMock(side_effect=RuntimeError("provider temporarily unavailable")),
        ):
            row, _ = await create_solution_from_intent(
                self.db,
                tenant_id=self.tenant.id,
                user_id=self.user.id,
                name="Student Grades Recorder",
                objective="Record student grades and save them for later review.",
                service=self.service,
            )
        await self.db.commit()

        self.assertEqual(row.lifecycle_status, LifecycleStatus.FAILED)
        self.assertEqual(row.preview_state, "unavailable")
        self.assertIsNone(row.current_version_reference)
        payload = solution_json(row)
        self.assertEqual(payload["generation"]["status"], "retryable")
        self.assertEqual(payload["generation"]["stage"], "proposal")
        self.assertIn("provider temporarily unavailable", payload["generation"]["error"])

        jobs = await self._jobs(row.id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].status, "failed")
        self.assertEqual(jobs[0].attempt, 1)

        synced = await self.service.get(self.db, self.tenant.id, row.id)
        self.assertEqual(synced.lifecycle_status, LifecycleStatus.FAILED)
        self.assertEqual(synced.preview_state, "unavailable")
        with self.assertRaisesRegex(LookupError, "not ready"):
            _, runtime = await self.service.resolve(self.db, self.tenant.id, row.id)
            await self.service.preview_target(self.db, self.tenant.id, synced, runtime)

    async def test_success_requires_non_bootstrap_applied_version(self):
        row, _ = await create_solution_from_intent(
            self.db,
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            name="Customer Notebook",
            objective="Build a lightweight customer notebook.",
            service=self.service,
        )
        await self.db.commit()

        payload = solution_json(row)
        self.assertEqual(row.lifecycle_status, LifecycleStatus.PREVIEW_READY)
        self.assertEqual(row.preview_state, "ready")
        self.assertIsNotNone(row.current_version_reference)
        self.assertEqual(payload["generation"]["status"], "applied")
        self.assertNotEqual(
            payload["generation"]["versionId"],
            payload["generation"]["bootstrapVersionId"],
        )
        jobs = await self._jobs(row.id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].status, "succeeded")

    async def test_failed_generation_retries_from_stored_owner_objective(self):
        with patch.object(
            ApplicationBuilderService,
            "propose",
            new=AsyncMock(side_effect=RuntimeError("first provider path failed")),
        ):
            row, _ = await create_solution_from_intent(
                self.db,
                tenant_id=self.tenant.id,
                user_id=self.user.id,
                name="Customer Notebook",
                objective="Build a lightweight customer notebook.",
                service=self.service,
            )
        await self.db.commit()
        self.assertEqual(row.lifecycle_status, LifecycleStatus.FAILED)

        retried = await retry_solution_initial_generation(
            self.db,
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            solution_id=row.id,
            service=self.service,
        )
        await self.db.commit()

        self.assertEqual(retried.lifecycle_status, LifecycleStatus.PREVIEW_READY)
        self.assertEqual(retried.preview_state, "ready")
        payload = solution_json(retried)
        self.assertEqual(payload["generation"]["attempt"], 2)
        self.assertEqual(payload["generation"]["status"], "applied")
        jobs = await self._jobs(row.id)
        self.assertEqual([job.status for job in jobs], ["failed", "succeeded"])
        self.assertEqual([job.attempt for job in jobs], [1, 2])


if __name__ == "__main__":
    unittest.main()
