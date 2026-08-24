import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.coding_harness.build_service import submit_source_build
from packages.coding_harness.execution_loop import _await_runner_build
from packages.custom_software.runner_contracts import BuildSubmission, HealthCheck
from packages.database.custom_software_models import (
    GeneratedSourceBundle,
    RunnerBuildRecord,
    SoftwarePlanRecord,
)
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.schema import import_all_models


class AsyncRunnerPollingTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_runner_ack_is_not_treated_as_finished_failure(self):
        queued = SimpleNamespace(id="build-1", state="queued")
        building = SimpleNamespace(id="build-1", state="building")
        ready = SimpleNamespace(id="build-1", state="preview_ready")
        refresh = AsyncMock(side_effect=[building, ready])

        with patch(
            "packages.coding_harness.execution_loop.refresh_build",
            new=refresh,
        ), patch(
            "packages.coding_harness.execution_loop._runner_poll_interval",
            return_value=0.001,
        ):
            result = await _await_runner_build(object(), queued, adapter=object())

        self.assertIs(result, ready)
        self.assertEqual(refresh.await_count, 2)

    async def test_evidence_bearing_failure_returns_without_false_preview(self):
        queued = SimpleNamespace(id="build-2", state="queued")
        failed = SimpleNamespace(id="build-2", state="tests_failed")
        refresh = AsyncMock(return_value=failed)

        with patch(
            "packages.coding_harness.execution_loop.refresh_build",
            new=refresh,
        ), patch(
            "packages.coding_harness.execution_loop._runner_poll_interval",
            return_value=0.001,
        ):
            result = await _await_runner_build(object(), queued, adapter=object())

        self.assertIs(result, failed)
        self.assertEqual(result.state, "tests_failed")

    async def test_ambiguous_queued_build_without_remote_id_is_resubmitted_idempotently(self):
        import_all_models()
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        db = async_sessionmaker(engine, expire_on_commit=False)()
        try:
            user = AppUser(email="runner-recovery@example.test", password_hash="x")
            tenant = Tenant(name="Runner Recovery")
            db.add_all([user, tenant])
            await db.flush()
            plan = SoftwarePlanRecord(
                tenant_id=tenant.id,
                prompt="Build a durable app",
                current_version=1,
                approved_version=1,
                status="approved",
                created_by=user.id,
            )
            db.add(plan)
            await db.flush()
            digest = "sha256:" + "0" * 64
            source = GeneratedSourceBundle(
                tenant_id=tenant.id,
                plan_id=plan.id,
                plan_version=1,
                source_version=1,
                application_id=f"plan-{plan.id}",
                bundle_digest=digest,
                manifest_json="{}",
                files_json="[]",
                provenance_json="{}",
                created_by=user.id,
            )
            db.add(source)
            await db.flush()
            submission = BuildSubmission(
                workspaceId=tenant.id,
                applicationId=source.application_id,
                planVersion=1,
                sourceVersion=1,
                stackId="operly-fullstack-v1",
                stackVersion=1,
                sourceBundleDigest=digest,
                operations=[
                    "stage_source",
                    "static_analysis",
                    "build",
                    "test",
                    "start",
                    "health_check",
                    "acceptance_test",
                ],
                healthCheck=HealthCheck(),
                idempotencyKey="durable-runner-build",
            )
            local = RunnerBuildRecord(
                tenant_id=tenant.id,
                plan_id=plan.id,
                source_bundle_id=source.id,
                runner_job_id=None,
                idempotency_key=submission.idempotencyKey,
                state="queued",
                runner_implementation="test",
                isolation_profile="isolated-test",
                submission_json=submission.model_dump_json(),
                result_json="{}",
                created_by=user.id,
            )
            db.add(local)
            await db.commit()

            class CountingAdapter:
                implementation = "test"
                isolation_profile = "isolated-test"

                def __init__(self):
                    self.submissions = 0

                async def capabilities(self):
                    return None

                async def submit(self, submitted, bundle):
                    self.submissions += 1
                    self.last_key = submitted.idempotencyKey
                    return {"jobId": "remote-job-1", "state": "queued"}

            adapter = CountingAdapter()
            fake_bundle = SimpleNamespace(digest=digest)
            with patch(
                "packages.coding_harness.build_service.source_bundle_from_record",
                return_value=fake_bundle,
            ), patch(
                "packages.coding_harness.build_service.attach_transport_grants",
                side_effect=lambda value: value,
            ):
                recovered = await submit_source_build(
                    db,
                    tenant.id,
                    user.id,
                    plan,
                    SimpleNamespace(),
                    source,
                    submission.idempotencyKey,
                    adapter=adapter,
                )
                again = await submit_source_build(
                    db,
                    tenant.id,
                    user.id,
                    plan,
                    SimpleNamespace(),
                    source,
                    submission.idempotencyKey,
                    adapter=adapter,
                )

            self.assertEqual(adapter.submissions, 1)
            self.assertEqual(adapter.last_key, submission.idempotencyKey)
            self.assertEqual(recovered.id, local.id)
            self.assertEqual(recovered.runner_job_id, "remote-job-1")
            self.assertEqual(again.id, local.id)
            self.assertEqual(again.runner_job_id, "remote-job-1")
        finally:
            await db.close()
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
