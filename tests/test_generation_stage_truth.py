import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.schema import import_all_models
from packages.solutions.composer import create_solution_from_intent
from packages.solutions.generation_worker import process_generation_job
from packages.solutions.service import LifecycleStatus, SolutionService, solution_json


@pytest.mark.asyncio
async def test_runner_test_failure_is_not_reported_as_source_generation():
    import_all_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            user = AppUser(email="stage-truth@example.test", password_hash="x")
            tenant = Tenant(name="Stage Truth")
            db.add_all([user, tenant])
            await db.flush()
            service = SolutionService()
            row, _ = await create_solution_from_intent(
                db,
                tenant_id=tenant.id,
                user_id=user.id,
                name="Stage truth fixture",
                objective="Create a tiny generated app.",
                service=service,
            )
            await db.commit()
            job = next(item for item in row.jobs if item.job_type == "generated_generation") if getattr(row, "jobs", None) else None
            if job is None:
                from sqlalchemy import select
                from packages.database.product_models import SolutionJob
                job = await db.scalar(select(SolutionJob).where(SolutionJob.solution_id == row.id))
            job.status = "running"
            job.locked_by = "worker-stage-test"
            job.started_at = datetime.utcnow()
            job.lease_expires_at = datetime.utcnow() + timedelta(minutes=2)
            await db.commit()

            plan_row = SimpleNamespace(id="plan-stage", approved_version=1, status="approved")
            plan = SimpleNamespace()
            source = SimpleNamespace(id="source-stage", source_version=2)
            build = SimpleNamespace(
                id="build-stage",
                state="tests_failed",
                failure_classification="test_failure",
            )

            async def fake_build(*args, **kwargs):
                progress = kwargs["progress_callback"]
                await progress("runner_build", "running", {"buildId": build.id})
                await progress(
                    "runner_test",
                    "failed",
                    {
                        "buildId": build.id,
                        "classification": "test_failure",
                        "message": "AssertionError: expected clock-out QR to close the active shift",
                        "buildState": "tests_failed",
                        "runnerEventState": "testing",
                        "runnerExitCode": 1,
                        "attempt": 3,
                    },
                )
                return build, source, []

            with patch(
                "packages.solutions.generation_worker._ensure_plan",
                new=AsyncMock(return_value=(plan_row, plan)),
            ), patch(
                "packages.solutions.generation_worker.build_with_repair",
                new=fake_build,
            ):
                await process_generation_job(db, job)

            await db.refresh(row)
            await db.refresh(job)
            generation = solution_json(row)["generation"]
            assert row.lifecycle_status == LifecycleStatus.FAILED
            assert generation["stage"] == "runner_test"
            assert generation["stage"] != "source_generation"
            assert generation["error"] == "AssertionError: expected clock-out QR to close the active shift"
            context = json.loads(row.context_json)["initialGeneration"]
            assert context["failureClassification"] == "test_failure"
            assert context["failureMessage"] == "AssertionError: expected clock-out QR to close the active shift"
            assert context["buildState"] == "tests_failed"
            assert context["runnerEventState"] == "testing"
            assert context["runnerExitCode"] == 1
            assert context["runnerAttempt"] == 3
            evidence = json.loads(job.evidence_json)
            assert evidence["failedStage"] == "runner_test"
            assert evidence["failureClassification"] == "test_failure"
            assert evidence["failureMessage"] == "AssertionError: expected clock-out QR to close the active shift"
            assert evidence["runnerExitCode"] == 1
            logs = json.loads(job.log_json)
            assert any(item["stage"] == "runner_build" for item in logs)
            assert any(item["stage"] == "runner_test" for item in logs)
    finally:
        await engine.dispose()
