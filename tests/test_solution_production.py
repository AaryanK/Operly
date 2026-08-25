import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.company.events import query_events
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.product_models import SolutionDeployment, SolutionJob
from packages.database.schema import import_all_models
from packages.software_projects import ProjectState, SoftwareProjectService, SoftwareSourceService
from packages.solutions import LifecycleStatus, SolutionService
from packages.solutions.deployment import ManagedStaticDeploymentProvider, UnconfiguredDeploymentProvider
from packages.solutions.production import JobStatus, JobType, ProductionService, transition


class FailedHealthProvider(ManagedStaticDeploymentProvider):
    async def health(self, result):
        return False, {"reason": "deliberate_health_failure"}


class ProductionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.user = AppUser(email="publish@test", password_hash="x")
        self.a = Tenant(name="A")
        self.b = Tenant(name="B")
        self.db.add_all([self.user, self.a, self.b])
        await self.db.flush()
        self.solutions = SolutionService()
        self.projects = SoftwareProjectService()
        self.sources = SoftwareSourceService()
        self.tmp = tempfile.TemporaryDirectory()
        self.solution, self.project, self.v1 = await self._create_static_solution(
            self.a.id,
            "Live Acme",
            "<!doctype html><html><body><h1>Live Acme</h1></body></html>",
        )
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()
        self.tmp.cleanup()

    async def _create_static_solution(self, tenant_id, name, html):
        project = await self.projects.create(
            self.db,
            workspace_id=tenant_id,
            user_id=self.user.id,
            name=name,
            description="A real public business presence",
        )
        source = await self.sources.persist(
            self.db,
            tenant_id=tenant_id,
            project_id=project.id,
            user_id=self.user.id,
            files={"index.html": html},
            runtime_profile="static-web-js",
            provenance={"test": True},
            change_summary="Initial canonical source",
        )
        await self.projects.set_execution_state(
            self.db,
            workspace_id=tenant_id,
            project_id=project.id,
            source_version_id=source.id,
            runtime_id="static-web-js",
            state=ProjectState.APPROVED,
        )
        record = await self.projects.record(self.db, tenant_id, project.id)
        solution = await self.solutions.create_software_solution(
            self.db,
            tenant_id=tenant_id,
            user_id=self.user.id,
            project=record,
            objective=record.description,
        )
        return solution, record, source

    async def _new_source(self, title):
        return await self.sources.persist(
            self.db,
            tenant_id=self.a.id,
            project_id=self.project.id,
            user_id=self.user.id,
            files={
                "index.html": f"<!doctype html><html><body><h1>{title}</h1></body></html>"
            },
            runtime_profile="static-web-js",
            provenance={"test": True},
            change_summary=title,
        )

    async def test_job_transitions_reject_invalid_state(self):
        job = SolutionJob(
            tenant_id=self.a.id,
            solution_id=self.solution.id,
            source_version_reference=self.v1.id,
            job_type=JobType.PUBLISH,
            status=JobStatus.QUEUED,
            idempotency_key="transition-test",
        )
        with self.assertRaisesRegex(ValueError, "Invalid Solution job transition"):
            transition(job, JobStatus.SUCCEEDED)
        transition(job, JobStatus.RUNNING)
        transition(job, JobStatus.SUCCEEDED)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.ended_at)

    async def test_full_real_static_publish_idempotency_and_events(self):
        production = ProductionService(
            self.solutions, ManagedStaticDeploymentProvider(self.tmp.name)
        )
        job, row = await production.publish(
            self.db,
            self.a.id,
            self.solution.id,
            self.user.id,
            idempotency_key="publish-once",
        )
        self.assertEqual(
            (job.status, row.lifecycle_status, row.production_state),
            (JobStatus.SUCCEEDED, LifecycleStatus.LIVE, "live"),
        )
        deployment = await self.db.scalar(
            select(SolutionDeployment).where(SolutionDeployment.job_id == job.id)
        )
        self.assertTrue(Path(deployment.artifact_reference).is_file())
        html = Path(deployment.artifact_reference).read_text(encoding="utf-8")
        self.assertIn("Live Acme", html)
        self.assertEqual(deployment.health_state, "healthy")

        same_job, _ = await production.publish(
            self.db,
            self.a.id,
            self.solution.id,
            self.user.id,
            idempotency_key="publish-once",
        )
        self.assertEqual(same_job.id, job.id)
        self.assertEqual(
            await self.db.scalar(select(func.count(SolutionDeployment.id))), 1
        )
        job_types = set(
            (
                await self.db.scalars(
                    select(SolutionJob.job_type).where(
                        SolutionJob.solution_id == self.solution.id
                    )
                )
            ).all()
        )
        self.assertTrue({JobType.BUILD, JobType.VERIFY, JobType.PUBLISH} <= job_types)
        events = {
            item.event_type for item in await query_events(self.db, self.a.id, limit=100)
        }
        self.assertTrue(
            {
                "solution.publish.requested",
                "solution.publish.started",
                "solution.publish.succeeded",
            }
            <= events
        )

    async def test_failed_health_preserves_previous_live_and_bounds_redacted_logs(self):
        good = ProductionService(
            self.solutions, ManagedStaticDeploymentProvider(self.tmp.name)
        )
        _, row = await good.publish(
            self.db, self.a.id, self.solution.id, self.user.id, idempotency_key="good"
        )
        old_url = row.production_url
        await self._new_source("Second version")

        failed, row = await ProductionService(
            self.solutions, FailedHealthProvider(self.tmp.name)
        ).publish(
            self.db,
            self.a.id,
            self.solution.id,
            self.user.id,
            idempotency_key="bad-health",
        )
        self.assertEqual(failed.failure_classification, "health_check_failure")
        self.assertEqual(row.lifecycle_status, LifecycleStatus.LIVE)
        self.assertEqual(row.production_url, old_url)
        self.assertEqual(
            await self.db.scalar(select(func.count(SolutionDeployment.id))), 1
        )
        transition(
            failed,
            failed.status,
            log="token=super-secret-value " + ("x" * 100000),
        )
        self.assertNotIn("super-secret-value", failed.log_json)
        self.assertLessEqual(len(failed.log_json), 32000)

    async def test_unconfigured_is_truthful_and_tenant_scoped(self):
        job, row = await ProductionService(
            self.solutions, UnconfiguredDeploymentProvider()
        ).publish(
            self.db,
            self.a.id,
            self.solution.id,
            self.user.id,
            idempotency_key="unconfigured",
        )
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertEqual(job.failure_classification, "provider_unconfigured")
        self.assertEqual(row.lifecycle_status, LifecycleStatus.FAILED)
        with self.assertRaises(LookupError):
            await ProductionService(
                self.solutions, ManagedStaticDeploymentProvider(self.tmp.name)
            ).publish(self.db, self.b.id, self.solution.id, self.user.id)

    async def test_rollback_redeploys_previous_verified_source_version(self):
        production = ProductionService(
            self.solutions, ManagedStaticDeploymentProvider(self.tmp.name)
        )
        first, _ = await production.publish(
            self.db, self.a.id, self.solution.id, self.user.id, idempotency_key="v1"
        )
        first_deployment = await self.db.scalar(
            select(SolutionDeployment).where(SolutionDeployment.job_id == first.id)
        )
        second_source = await self._new_source("Version Two")
        _, row = await production.publish(
            self.db, self.a.id, self.solution.id, self.user.id, idempotency_key="v2"
        )
        self.assertEqual(row.current_version_reference, second_source.id)

        rollback, row = await production.rollback(
            self.db, self.a.id, self.solution.id, self.user.id
        )
        self.assertEqual(rollback.status, JobStatus.SUCCEEDED)
        self.assertEqual(row.current_version_reference, first_deployment.version_reference)
        events = {
            item.event_type for item in await query_events(self.db, self.a.id, limit=100)
        }
        self.assertIn("solution.rollback.succeeded", events)


if __name__ == "__main__":
    unittest.main()
