import json
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.custom_software.runner_contracts import BuildSubmission, HealthCheck
from packages.custom_software.runner_service import apply_runner_response
from packages.database.custom_software_models import (
    GeneratedSourceBundle,
    RunnerBuildEvent,
    RunnerBuildRecord,
    SoftwarePlanRecord,
)
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.schema import import_all_models


class RunnerFailureObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()

        self.user = AppUser(email="runner-failure-observability@example.test", password_hash="x")
        self.tenant = Tenant(name="Runner Failure Observability")
        self.db.add_all([self.user, self.tenant])
        await self.db.flush()
        self.plan = SoftwarePlanRecord(
            tenant_id=self.tenant.id,
            prompt="Build an attendance app",
            current_version=1,
            approved_version=1,
            status="approved",
            created_by=self.user.id,
        )
        self.db.add(self.plan)
        await self.db.flush()
        digest = "sha256:" + "0" * 64
        self.source = GeneratedSourceBundle(
            tenant_id=self.tenant.id,
            plan_id=self.plan.id,
            plan_version=1,
            source_version=1,
            application_id=f"plan-{self.plan.id}",
            bundle_digest=digest,
            manifest_json="{}",
            files_json="[]",
            provenance_json="{}",
            created_by=self.user.id,
        )
        self.db.add(self.source)
        await self.db.flush()
        self.submission = BuildSubmission(
            workspaceId=self.tenant.id,
            applicationId=self.source.application_id,
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
            idempotencyKey="runner-failure-observability",
        )
        self.build = RunnerBuildRecord(
            tenant_id=self.tenant.id,
            plan_id=self.plan.id,
            source_bundle_id=self.source.id,
            runner_job_id="remote-build-1",
            idempotency_key=self.submission.idempotencyKey,
            state="acceptance_testing",
            runner_implementation="test",
            isolation_profile="isolated-test",
            submission_json=self.submission.model_dump_json(),
            result_json="{}",
            created_by=self.user.id,
        )
        self.db.add(self.build)
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def test_missing_classification_at_acceptance_is_logged_and_normalized(self):
        response = {
            "jobId": "remote-build-1",
            "state": "failed",
            "result": {
                "buildSuccess": True,
                "testSuccess": True,
                "processStartSuccess": True,
                "healthCheckSuccess": True,
                "acceptanceCheckSuccess": False,
                "previewAvailable": False,
                "failureEvidence": {
                    "message": "POST /api/clock-in returned 500",
                    "endpoint": "/api/clock-in",
                },
            },
        }

        row = await apply_runner_response(self.db, self.build, response, self.submission)

        self.assertEqual(row.state, "acceptance_failed")
        self.assertEqual(row.failure_classification, "acceptance_test_failure")
        persisted = json.loads(row.result_json)
        self.assertEqual(
            persisted["failureEvidence"]["message"],
            "POST /api/clock-in returned 500",
        )
        events = list(
            (
                await self.db.scalars(
                    select(RunnerBuildEvent)
                    .where(RunnerBuildEvent.build_id == row.id)
                    .order_by(RunnerBuildEvent.sequence)
                )
            ).all()
        )
        self.assertEqual(events[0].event_type, "runner_failure_observed")
        self.assertIn("POST /api/clock-in returned 500", events[0].details_json)
        self.assertEqual(events[-1].event_type, "failure")
        self.assertEqual(events[-1].state, "acceptance_failed")

    async def test_truly_unknown_acceptance_failure_does_not_jump_to_generic_failed(self):
        response = {
            "jobId": "remote-build-1",
            "state": "failed",
            "result": {
                "failureEvidence": {"message": "Acceptance gate stopped unexpectedly"}
            },
        }

        row = await apply_runner_response(self.db, self.build, response, self.submission)

        self.assertEqual(row.state, "acceptance_failed")
        self.assertIn("Acceptance gate stopped unexpectedly", row.result_json)

    async def test_runner_reported_service_binding_classification_survives_phase_fallback(self):
        self.build.state = "provisioning"
        await self.db.commit()
        response = {
            "jobId": "remote-build-1",
            "state": "failed",
            "result": {
                "failureEvidence": {
                    "classification": "service_binding_failure",
                    "message": "Relational migration gateway request failed",
                }
            },
        }

        row = await apply_runner_response(self.db, self.build, response, self.submission)

        self.assertEqual(row.state, "provision_failed")
        self.assertEqual(row.failure_classification, "service_binding_failure")
        persisted = json.loads(row.result_json)
        self.assertEqual(
            persisted["failureEvidence"]["classification"],
            "service_binding_failure",
        )


if __name__ == "__main__":
    unittest.main()
