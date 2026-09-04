from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.agent_runtime import (
    AgentPlan,
    AgentPlanStep,
    AgentRunStatus,
    AgentRuntimeSettings,
    AgentStepResult,
    AgentStepStatus,
    DurableAgentOrchestrator,
    GovernedAgentRuntime,
    stable_step_request_id,
)
from packages.agent_runtime.store import create_run, request_cancellation
from packages.database.agent_runtime_models import AgentRuntimeRun
from packages.database.db import Base
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models
from packages.kernel.contracts import (
    AuthorizationDecision,
    CapabilityRisk,
    CapabilitySpec,
    RuntimeResponse,
)
from packages.kernel.idempotency import IdempotencyInProgress
from packages.kernel.registry import CapabilityRegistry
from packages.kernel.runtime import RuntimeExecutionError
from packages.security.execution_context import (
    ExecutionContext,
    ScopeKind,
    resolve_execution_context,
)
from packages.security.surfaces import SurfaceKind


def write_capability() -> CapabilitySpec:
    return CapabilitySpec(
        id="records.write",
        version="1",
        display_name="records.write",
        description="test write",
        provider_id="fake",
        scopes=frozenset({"workspace"}),
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        risk=CapabilityRisk.MEDIUM,
        approval_required=False,
    )


class ErrorKernel:
    def __init__(self, *, code: str = "runtime_unavailable") -> None:
        self.registry = CapabilityRegistry()
        self.registry.register(write_capability())
        self.code = code

    async def execute(self, db, *, context, request):
        del db, context, request
        raise RuntimeExecutionError(
            "provider response was unavailable",
            run_id="kernel-uncertain-run",
            code=self.code,
            status_code=503,
        )


def execution_context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        membership_id="membership-1",
        role="owner",
        permissions=frozenset({"workspace:read"}),
        channel="web",
        conversation_id="conversation-1",
        scope_kind=ScopeKind.WORKSPACE,
        principal_id="user:user-1",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
    )


class AgentMutationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_running_kernel_claim_becomes_execution_uncertain(self):
        runtime = GovernedAgentRuntime(
            kernel=ErrorKernel(),
            settings=AgentRuntimeSettings(enabled=True),
        )
        with patch(
            "packages.agent_runtime.runtime.find_completed_request",
            new=AsyncMock(side_effect=IdempotencyInProgress("still running")),
        ):
            result = await runtime.execute_step(
                object(),
                context=execution_context(),
                run_id="run-uncertain",
                goal="write record",
                step=AgentPlanStep("write", "records.write", {"value": 1}),
            )

        self.assertEqual(result.status, AgentStepStatus.EXECUTION_UNCERTAIN)
        self.assertEqual(result.error_code, "execution_outcome_uncertain")
        self.assertIn("must be reconciled", result.error)

    async def test_request_in_progress_is_uncertain_even_if_claim_probe_races_empty(self):
        runtime = GovernedAgentRuntime(
            kernel=ErrorKernel(code="request_in_progress"),
            settings=AgentRuntimeSettings(enabled=True),
        )
        with patch(
            "packages.agent_runtime.runtime.find_completed_request",
            new=AsyncMock(return_value=None),
        ):
            result = await runtime.execute_step(
                object(),
                context=execution_context(),
                run_id="run-in-progress",
                goal="write record",
                step=AgentPlanStep("write", "records.write", {"value": 2}),
            )

        self.assertEqual(result.status, AgentStepStatus.EXECUTION_UNCERTAIN)

    async def test_pre_reservation_failure_remains_ordinary_failed_step(self):
        runtime = GovernedAgentRuntime(
            kernel=ErrorKernel(),
            settings=AgentRuntimeSettings(enabled=True),
        )
        with patch(
            "packages.agent_runtime.runtime.find_completed_request",
            new=AsyncMock(return_value=None),
        ):
            result = await runtime.execute_step(
                object(),
                context=execution_context(),
                run_id="run-failed",
                goal="write record",
                step=AgentPlanStep("write", "records.write", {"value": 3}),
            )

        self.assertEqual(result.status, AgentStepStatus.FAILED)
        self.assertEqual(result.error_code, "runtime_unavailable")

    async def test_completed_claim_replay_wins_over_observed_kernel_error(self):
        runtime = GovernedAgentRuntime(
            kernel=ErrorKernel(),
            settings=AgentRuntimeSettings(enabled=True),
        )
        replay = RuntimeResponse(
            run_id="kernel-completed-run",
            status="completed",
            capability_id="records.write",
            decision=AuthorizationDecision.ALLOW,
            result={"ok": True},
            done=True,
            trace=(),
        )
        with patch(
            "packages.agent_runtime.runtime.find_completed_request",
            new=AsyncMock(return_value=replay),
        ):
            result = await runtime.execute_step(
                object(),
                context=execution_context(),
                run_id="run-replay",
                goal="write record",
                step=AgentPlanStep("write", "records.write", {"value": 4}),
            )

        self.assertEqual(result.status, AgentStepStatus.COMPLETED)
        self.assertEqual(result.kernel_run_id, "kernel-completed-run")
        self.assertEqual(result.result, {"ok": True})


class UncertainDurableRuntime:
    def require_enabled(self) -> None:
        return None

    def preflight_plan(self, plan):
        del plan
        return None

    async def execute_step(self, db, *, context, run_id, goal, step):
        del goal
        await request_cancellation(db, context=context, run_id=run_id)
        return AgentStepResult(
            step_id=step.step_id,
            capability_id=step.capability_id,
            request_id=stable_step_request_id(run_id, step.step_id),
            status=AgentStepStatus.EXECUTION_UNCERTAIN,
            kernel_run_id="kernel-uncertain-run",
            error_code="execution_outcome_uncertain",
            error="external mutation outcome requires reconciliation",
        )


class AgentUncertainDurabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as db:
            db.add(AppUser(id="user-1", email="user1@example.com", display_name="User 1"))
            db.add(Tenant(id="workspace-1", name="Workspace 1", slug="workspace-1"))
            db.add(
                TenantMember(
                    id="membership-1",
                    tenant_id="workspace-1",
                    user_id="user-1",
                    role="owner",
                )
            )
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_uncertainty_is_durable_terminal_and_dominates_cancellation(self):
        worker = DurableAgentOrchestrator(
            runtime=UncertainDurableRuntime(),
            heartbeat_session_factory=self.sessions,
            lease_seconds=60,
        )
        async with self.sessions() as db:
            context = await resolve_execution_context(
                db,
                workspace_id="workspace-1",
                user_id="user-1",
                channel="web",
                surface=SurfaceKind.WORKSPACE_PRIVATE,
                conversation_id="conversation-1",
                require_membership=True,
            )
            await create_run(
                db,
                context=context,
                plan=AgentPlan(
                    run_id="durable-uncertain",
                    goal="write once",
                    steps=(AgentPlanStep("write", "records.write", {"value": 1}),),
                ),
            )
            await db.commit()

            result = await worker.run_once(
                db,
                run_id="durable-uncertain",
                lease_token="worker-a",
            )
            stored = await db.get(AgentRuntimeRun, "durable-uncertain")

        self.assertIsNotNone(result)
        self.assertEqual(result.status, AgentRunStatus.EXECUTION_UNCERTAIN)
        self.assertTrue(result.done)
        self.assertEqual(stored.status, "execution_uncertain")
        self.assertTrue(stored.cancellation_requested)
        self.assertEqual(stored.error_code, "execution_outcome_uncertain")
        self.assertIsNone(stored.lease_token)
        self.assertIsNone(stored.lease_until)
        self.assertIsNotNone(stored.finished_at)


if __name__ == "__main__":
    unittest.main()
