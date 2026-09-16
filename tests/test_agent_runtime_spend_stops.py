from __future__ import annotations

import unittest

from packages.agent_runtime.inference import AgentInferenceError
from packages.agent_runtime.interactive import Runtime1Agent
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.kernel.contracts import (
    AuthorizationDecision,
    CapabilityRisk,
    CapabilitySpec,
    RuntimeResponse,
)
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


def personal_context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=None,
        user_id="user-spend-stop",
        membership_id=None,
        role="personal_owner",
        permissions=frozenset({"workspace:read"}),
        channel="web",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id="conversation-spend-stop",
        scope_kind=ScopeKind.PERSONAL,
        principal_id="user:user-spend-stop",
        workspace_mode="personal",
    )


def read_capability() -> CapabilitySpec:
    return CapabilitySpec(
        id="records.read",
        version="1",
        display_name="Read record",
        description="Read one personal record.",
        provider_id="fixture",
        scopes=frozenset({"personal"}),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        permissions=(),
        risk=CapabilityRisk.READ_ONLY,
        tags=frozenset({"records", "read"}),
    )


class NoToolBudgetStopModel:
    async def interpret(self, request):
        del request
        return {
            "objective": "Explain the concept",
            "kind": "respond",
            "operations": ["respond"],
            "resource_hints": [],
            "requires_external_state": False,
            "requires_mutation": False,
            "requires_future_wait": False,
            "complexity": "simple",
        }

    async def respond(self, **kwargs):
        del kwargs
        raise AgentInferenceError(
            "task spend exhausted",
            code="inference_spend_budget_exhausted",
        )

    async def decide(self, **kwargs):
        del kwargs
        raise AssertionError("no-tool request must not enter the agent loop")


class ProgressThenBudgetStopModel:
    def __init__(self) -> None:
        self.decisions = 0

    async def interpret(self, request):
        del request
        return {
            "objective": "Read the record",
            "kind": "retrieve",
            "operations": ["retrieve"],
            "resource_hints": ["record"],
            "requires_external_state": True,
            "requires_mutation": False,
            "requires_future_wait": False,
            "complexity": "simple",
        }

    async def decide(self, **kwargs):
        del kwargs
        self.decisions += 1
        return {"move": "call", "capability_id": "records.read", "arguments": {}}

    async def respond(self, **kwargs):
        del kwargs
        raise AgentInferenceError(
            "model-call budget exhausted after tool success",
            code="inference_model_call_budget_exhausted",
        )


class FakeReadKernel:
    def __init__(self) -> None:
        self.spec = read_capability()
        self.calls = 0

    async def available_capabilities(self, db, *, context, query, limit):
        del db, context, query, limit
        return (self.spec,)

    async def execute(self, db, *, context, request):
        del db, context
        self.calls += 1
        self.assert_request(request)
        return RuntimeResponse(
            run_id="kernel-read-1",
            status="completed",
            capability_id=self.spec.id,
            decision=AuthorizationDecision.ALLOW,
            result={"record": {"id": "record-1", "value": "fixture"}},
            done=True,
            trace=(),
        )

    def assert_request(self, request):
        if request.capability_id != self.spec.id:
            raise AssertionError(f"unexpected capability: {request.capability_id}")

    @property
    def registry(self):
        return _FakeRegistry(self.spec)


class _FakeRegistry:
    def __init__(self, spec: CapabilitySpec) -> None:
        self.spec = spec

    def get(self, capability_id: str) -> CapabilitySpec:
        if capability_id != self.spec.id:
            raise KeyError(capability_id)
        return self.spec


class Runtime1SpendStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_tool_budget_exhaustion_returns_clear_stop_instead_of_generic_failure(self):
        agent = Runtime1Agent(
            model=NoToolBudgetStopModel(),
            settings=AgentRuntimeSettings(enabled=True),
        )
        result = await agent.run(
            None,
            context=personal_context(),
            message="Explain this concept.",
            kernel=None,
            run_id="run-no-tool-budget-stop",
        )

        self.assertEqual(result.error_code, "inference_spend_budget_exhausted")
        self.assertEqual(result.capability_calls, ())
        self.assertIn("spend or model-call limit", result.message)
        self.assertIn("No external capability call completed", result.message)

    async def test_spend_stop_after_completed_tool_preserves_truthful_progress(self):
        model = ProgressThenBudgetStopModel()
        kernel = FakeReadKernel()
        agent = Runtime1Agent(
            model=model,
            settings=AgentRuntimeSettings(enabled=True),
        )
        result = await agent.run(
            None,
            context=personal_context(),
            message="Read the record.",
            kernel=kernel,
            run_id="run-progress-budget-stop",
        )

        self.assertEqual(kernel.calls, 1)
        self.assertEqual(model.decisions, 1)
        self.assertEqual(result.error_code, "inference_model_call_budget_exhausted")
        self.assertEqual(result.capability_calls, ("records.read",))
        self.assertIn("Completed external capability calls before stopping", result.message)
        self.assertIn("records.read", result.message)


if __name__ == "__main__":
    unittest.main()
