import unittest
from unittest.mock import patch

from packages.model_runtime import InferenceRequest, InferenceResult, ModelInferenceError
from packages.model_runtime.registry import ModelPool
from packages.model_runtime.task_routing import TaskRoutedBusinessModel, classify_business_task


class _FailingModel:
    def __init__(self, model_id: str, *, provider: str, classification: str):
        self.id = model_id
        self.provider = provider
        self.tags = frozenset({"reliable"})
        self.capabilities = frozenset({"text", "tools"})
        self.traits = object()
        self.classification = classification
        self.calls = 0

    async def infer(self, request):
        del request
        self.calls += 1
        raise ModelInferenceError(
            f"{self.id} failed",
            classification=self.classification,
            retryable=False,
            provider=self.provider,
            model_id=self.id,
        )


class _SuccessModel:
    def __init__(self, model_id: str, *, provider: str):
        self.id = model_id
        self.provider = provider
        self.tags = frozenset({"reliable"})
        self.capabilities = frozenset({"text", "tools"})
        self.traits = object()
        self.calls = 0

    async def infer(self, request):
        del request
        self.calls += 1
        return InferenceResult(
            message={"role": "assistant", "content": "ok"},
            model_resource_id=self.id,
            provider=self.provider,
            provider_model_id=self.id,
            latency_ms=1,
            finish_reason="stop",
        )


class ModelRequestFailoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_specific_invalid_request_falls_through(self):
        first = _FailingModel(
            "tool-schema-incompatible",
            provider="provider-a",
            classification="invalid_request",
        )
        second = _SuccessModel("compatible", provider="provider-b")
        result = await ModelPool([first, second]).infer(
            InferenceRequest(
                messages=({"role": "user", "content": "use a tool"},),
                tools=({"type": "function", "function": {"name": "example"}},),
            )
        )
        self.assertEqual(result.provider_model_id, "compatible")
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)

    async def test_auth_failure_cools_provider_and_uses_other_provider(self):
        first = _FailingModel("bad-auth-a", provider="provider-a", classification="auth")
        same_provider = _SuccessModel("would-work-a", provider="provider-a")
        other_provider = _SuccessModel("works-b", provider="provider-b")
        result = await ModelPool([first, same_provider, other_provider]).infer(
            InferenceRequest(messages=({"role": "user", "content": "hello"},))
        )
        self.assertEqual(result.provider_model_id, "works-b")
        self.assertEqual(first.calls, 1)
        self.assertEqual(same_provider.calls, 0)
        self.assertEqual(other_provider.calls, 1)

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
        selected = _SuccessModel("sticky", provider="test")
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
        first = _SuccessModel("first", provider="test")
        second = _SuccessModel("second", provider="test")
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
