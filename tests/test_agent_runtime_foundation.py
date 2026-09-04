from __future__ import annotations

import inspect
import os
import unittest
from unittest.mock import patch

from packages.agent_runtime import (
    AgentBudget,
    AgentCancellation,
    AgentPlan,
    AgentPlanStep,
    AgentRunStatus,
    AgentRuntimeDisabled,
    AgentRuntimeSettings,
    AgentStepStatus,
    GovernedAgentRuntime,
    stable_step_request_id,
)
from packages.kernel.contracts import (
    AuthorizationDecision,
    CapabilityRisk,
    CapabilitySpec,
    RuntimeResponse,
)
from packages.kernel.registry import CapabilityRegistry
from packages.kernel.runtime import RuntimeExecutionError
from packages.security.execution_context import ExecutionContext, ScopeKind


def capability(capability_id: str, *, risk: CapabilityRisk) -> CapabilitySpec:
    return CapabilitySpec(
        id=capability_id,
        version="1",
        display_name=capability_id,
        description="test capability",
        provider_id="fake",
        scopes=frozenset({"workspace"}),
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        risk=risk,
        approval_required=risk is not CapabilityRisk.READ_ONLY,
    )


class FakeKernel:
    def __init__(self) -> None:
        self.registry = CapabilityRegistry()
        self.registry.register(capability("records.read", risk=CapabilityRisk.READ_ONLY))
        self.registry.register(capability("records.write", risk=CapabilityRisk.MEDIUM))
        self.calls: list[tuple[object, object]] = []
        self.require_approval = False
        self.cancel_after_first: AgentCancellation | None = None

    async def execute(self, db, *, context, request):
        del db
        self.calls.append((context, request))
        if self.cancel_after_first is not None and len(self.calls) == 1:
            self.cancel_after_first.cancel()
        if self.require_approval and request.capability_id == "records.write" and not request.approval_id:
            raise RuntimeExecutionError(
                "Approval is required before this capability can run",
                run_id="kernel-approval-run",
                code="approval_required",
                status_code=409,
                approval_id="approval-123",
            )
        return RuntimeResponse(
            run_id=f"kernel-{len(self.calls)}",
            status="completed",
            capability_id=request.capability_id,
            decision=AuthorizationDecision.ALLOW,
            result={"ok": True, "capability_id": request.capability_id},
            done=True,
            trace=(),
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
    )


class AgentRuntimeFoundationTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_is_disabled_by_default_and_legacy_flag_cannot_enable_it(self):
        kernel = FakeKernel()
        with patch.dict(
            os.environ,
            {"OPERLY_AGENT_RUNTIME_V2": "1", "OPERLY_AGENT_RUNTIME_ENABLED": "0"},
            clear=False,
        ):
            runtime = GovernedAgentRuntime(kernel=kernel)
        plan = AgentPlan(
            run_id="run-disabled",
            goal="read records",
            steps=(AgentPlanStep("read", "records.read"),),
        )
        with self.assertRaises(AgentRuntimeDisabled):
            await runtime.execute_plan(object(), context=execution_context(), plan=plan)
        self.assertEqual(kernel.calls, [])

    async def test_every_step_routes_through_kernel_with_same_trusted_context(self):
        kernel = FakeKernel()
        runtime = GovernedAgentRuntime(
            kernel=kernel,
            settings=AgentRuntimeSettings(enabled=True),
        )
        context = execution_context()
        plan = AgentPlan(
            run_id="run-governed",
            goal="read and update records",
            steps=(
                AgentPlanStep("read", "records.read", {"id": "a"}),
                AgentPlanStep("write", "records.write", {"id": "a", "value": 2}),
            ),
        )

        result = await runtime.execute_plan(object(), context=context, plan=plan)

        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        self.assertEqual(len(kernel.calls), 2)
        self.assertTrue(all(call_context is context for call_context, _ in kernel.calls))
        self.assertEqual(kernel.calls[0][1].conversation_id, "conversation-1")
        self.assertEqual(kernel.calls[1][1].request_id, stable_step_request_id("run-governed", "write"))
        self.assertLessEqual(len(kernel.calls[1][1].request_id), 160)

    async def test_mutation_budget_is_preflighted_before_any_capability_executes(self):
        kernel = FakeKernel()
        runtime = GovernedAgentRuntime(
            kernel=kernel,
            settings=AgentRuntimeSettings(enabled=True),
        )
        plan = AgentPlan(
            run_id="run-budget",
            goal="too many writes",
            budget=AgentBudget(max_steps=4, max_mutations=1),
            steps=(
                AgentPlanStep("write-1", "records.write", {"value": 1}),
                AgentPlanStep("write-2", "records.write", {"value": 2}),
            ),
        )

        result = await runtime.execute_plan(object(), context=execution_context(), plan=plan)

        self.assertEqual(result.status, AgentRunStatus.BUDGET_EXHAUSTED)
        self.assertEqual(result.error_code, "budget_exhausted")
        self.assertEqual(kernel.calls, [])

    async def test_approval_stops_plan_and_resume_reuses_exact_request_identity(self):
        kernel = FakeKernel()
        kernel.require_approval = True
        runtime = GovernedAgentRuntime(
            kernel=kernel,
            settings=AgentRuntimeSettings(enabled=True),
        )
        context = execution_context()
        waiting_plan = AgentPlan(
            run_id="run-approval",
            goal="update then read",
            steps=(
                AgentPlanStep("write", "records.write", {"value": 7}),
                AgentPlanStep("read", "records.read"),
            ),
        )

        waiting = await runtime.execute_plan(object(), context=context, plan=waiting_plan)

        self.assertEqual(waiting.status, AgentRunStatus.WAITING_APPROVAL)
        self.assertEqual(waiting.approval_id, "approval-123")
        self.assertEqual(waiting.next_step_id, "write")
        self.assertEqual(len(kernel.calls), 1)
        first_request_id = kernel.calls[0][1].request_id

        resumed_plan = AgentPlan(
            run_id="run-approval",
            goal="update then read",
            steps=(
                AgentPlanStep(
                    "write",
                    "records.write",
                    {"value": 7},
                    approval_id="approval-123",
                ),
            ),
        )
        resumed = await runtime.execute_plan(object(), context=context, plan=resumed_plan)

        self.assertEqual(resumed.status, AgentRunStatus.COMPLETED)
        self.assertEqual(kernel.calls[1][1].request_id, first_request_id)
        self.assertEqual(kernel.calls[1][1].approval_id, "approval-123")

    async def test_cancellation_stops_before_next_capability(self):
        kernel = FakeKernel()
        token = AgentCancellation()
        kernel.cancel_after_first = token
        runtime = GovernedAgentRuntime(
            kernel=kernel,
            settings=AgentRuntimeSettings(enabled=True),
        )
        plan = AgentPlan(
            run_id="run-cancel",
            goal="read twice",
            steps=(
                AgentPlanStep("read-1", "records.read"),
                AgentPlanStep("read-2", "records.read"),
            ),
        )

        result = await runtime.execute_plan(
            object(), context=execution_context(), plan=plan, cancellation=token
        )

        self.assertEqual(result.status, AgentRunStatus.CANCELLED)
        self.assertEqual(len(kernel.calls), 1)
        self.assertEqual(result.steps[-1].status, AgentStepStatus.CANCELLED)
        self.assertEqual(result.next_step_id, "read-2")

    def test_step_request_ids_are_stable_bounded_and_step_specific(self):
        first = stable_step_request_id("run-1", "step-1")
        self.assertEqual(first, stable_step_request_id("run-1", "step-1"))
        self.assertNotEqual(first, stable_step_request_id("run-1", "step-2"))
        self.assertNotEqual(first, stable_step_request_id("run-2", "step-1"))
        self.assertLessEqual(len(first), 160)

    def test_executor_has_no_direct_provider_execution_path(self):
        source = inspect.getsource(GovernedAgentRuntime)
        self.assertIn("self.kernel.execute", source)
        self.assertNotIn("providers.get", source)
        self.assertNotIn("provider.execute", source)

    def test_plan_rejects_duplicate_step_identity(self):
        with self.assertRaises(ValueError):
            AgentPlan(
                run_id="run-duplicate",
                goal="bad plan",
                steps=(
                    AgentPlanStep("same", "records.read"),
                    AgentPlanStep("same", "records.read"),
                ),
            )


if __name__ == "__main__":
    unittest.main()
