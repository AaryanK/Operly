from __future__ import annotations

import unittest

from packages.agent_runtime.planner import (
    AgentObservation,
    AgentPlanningError,
    AgentPlanningPolicy,
    GovernedAgentPlanner,
)
from packages.kernel.contracts import CapabilityRisk, CapabilitySpec
from packages.kernel.registry import CapabilityRegistry
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


class FakePlannerModel:
    def __init__(self, output) -> None:
        self.output = output
        self.payloads: list[dict] = []

    async def plan(self, payload):
        self.payloads.append(dict(payload))
        return self.output


def registry() -> CapabilityRegistry:
    value = CapabilityRegistry()
    value.register(
        CapabilitySpec(
            id="records.read",
            version="1",
            display_name="records.read",
            description="read records",
            provider_id="fake",
            scopes=frozenset({"workspace"}),
            input_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            permissions=frozenset({"records:read"}),
            risk=CapabilityRisk.READ_ONLY,
        )
    )
    return value


def context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        membership_id="membership-1",
        role="member",
        permissions=frozenset({"records:read"}),
        channel="web",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        conversation_id="conversation-1",
        scope_kind=ScopeKind.WORKSPACE,
        principal_id="user:user-1",
    )


class AgentRuntimePlannerLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_total_planner_input_is_bounded_before_model_call(self):
        model = FakePlannerModel({"done": True, "summary": "unused", "steps": []})
        planner = GovernedAgentPlanner(
            registry=registry(),
            model=model,
            policy=AgentPlanningPolicy(
                max_planner_input_bytes=4_096,
                max_observation_string_chars=8_000,
                max_observation_bytes=65_536,
            ),
        )
        with self.assertRaises(AgentPlanningError) as caught:
            await planner.plan(
                context=context(),
                run_id="run-input-bound",
                goal="read records",
                observations=(
                    AgentObservation(
                        "old-step",
                        "records.read",
                        "completed",
                        {"content": "X" * 6_000},
                    ),
                ),
            )
        self.assertEqual(caught.exception.code, "planner_input_too_large")
        self.assertEqual(model.payloads, [])

    async def test_observation_count_is_bounded_before_model_call(self):
        model = FakePlannerModel({"done": True, "summary": "unused", "steps": []})
        planner = GovernedAgentPlanner(
            registry=registry(),
            model=model,
            policy=AgentPlanningPolicy(max_observations=1),
        )
        with self.assertRaises(AgentPlanningError) as caught:
            await planner.plan(
                context=context(),
                run_id="run-observation-count",
                goal="read records",
                observations=(
                    AgentObservation("one", "records.read", "completed", {}),
                    AgentObservation("two", "records.read", "completed", {}),
                ),
            )
        self.assertEqual(caught.exception.code, "observation_budget_exhausted")
        self.assertEqual(model.payloads, [])

    async def test_non_finite_model_json_is_rejected(self):
        model = FakePlannerModel(
            {
                "done": False,
                "steps": [
                    {
                        "capability_id": "records.read",
                        "arguments": {"id": float("nan")},
                    }
                ],
            }
        )
        planner = GovernedAgentPlanner(registry=registry(), model=model)
        with self.assertRaises(AgentPlanningError) as caught:
            await planner.plan(
                context=context(),
                run_id="run-nonfinite",
                goal="read records",
            )
        self.assertEqual(caught.exception.code, "invalid_planner_json")


if __name__ == "__main__":
    unittest.main()
