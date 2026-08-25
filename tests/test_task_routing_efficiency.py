import unittest
from unittest.mock import patch

from packages.model_runtime import InferenceRequest, InferenceResult
from packages.model_runtime.task_routing import TaskRoutedBusinessModel, classify_business_task


class _SelectedModel:
    def __init__(self, model_id: str = "test:model") -> None:
        self.id = model_id
        self.calls = 0

    async def infer(self, request):
        del request
        self.calls += 1
        return InferenceResult(
            message={"role": "assistant", "content": "ok"},
            model_resource_id=self.id,
            provider="test",
            provider_model_id=self.id,
            latency_ms=1,
            finish_reason="stop",
        )


class TaskRoutingEfficiencyTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_execution_request_is_not_downgraded_to_planning(self):
        decision = classify_business_task(
            "Create the workflow and do not merely plan it."
        )

        self.assertEqual(decision.task_type, "bounded_operation")
        self.assertEqual(decision.role, "bounded_task")
        self.assertEqual(decision.tool_policy, "bounded_action_with_approval")
        self.assertIn("execution ownership", decision.reason)

    def test_real_planning_request_still_routes_to_planner(self):
        decision = classify_business_task("Plan how we should create the workflow.")

        self.assertEqual(decision.task_type, "planning")
        self.assertEqual(decision.role, "planner")
        self.assertEqual(decision.tool_policy, "read_then_propose")

    async def test_routed_model_reuses_resolved_model_for_identical_requirements(self):
        selected = _SelectedModel()
        model = TaskRoutedBusinessModel()
        request = InferenceRequest(
            messages=({"role": "user", "content": "Hello there"},),
        )

        with patch(
            "packages.model_runtime.task_routing.model_for_requirements",
            return_value=selected,
        ) as resolver:
            await model.infer(request)
            await model.infer(request)

        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(selected.calls, 2)

    async def test_routed_model_reselects_when_requirements_change(self):
        first = _SelectedModel("test:first")
        second = _SelectedModel("test:second")
        model = TaskRoutedBusinessModel()
        plain_request = InferenceRequest(
            messages=({"role": "user", "content": "Hello there"},),
        )
        tool_request = InferenceRequest(
            messages=({"role": "user", "content": "Hello there"},),
            tools=(
                {
                    "type": "function",
                    "function": {
                        "name": "example",
                        "description": "Example capability",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ),
        )

        with patch(
            "packages.model_runtime.task_routing.model_for_requirements",
            side_effect=[first, second],
        ) as resolver:
            await model.infer(plain_request)
            await model.infer(tool_request)

        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)


if __name__ == "__main__":
    unittest.main()
