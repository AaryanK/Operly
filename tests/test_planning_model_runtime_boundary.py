import unittest
from pathlib import Path

from packages.custom_software.live_planning import PlanningContextPacket
from packages.custom_software.model_planning_client import ModelPlanningClient
from packages.model_runtime import InferenceResult
from packages.custom_software.live_planning import RequirementAnalysis


class _FakeModel:
    id = "fake-planner"

    async def infer(self, request):
        return InferenceResult(
            message={
                "role": "assistant",
                "content": '{"root_objective":"Build a tiny operations tool","requirements":[],"global_constraints":[],"ambiguities":[],"conflicts":[],"planning_assumptions":[]}',
            },
            model_resource_id="fake-planner",
            provider="test-provider",
            provider_model_id="test-model",
            latency_ms=3,
            usage=None,
            finish_reason="stop",
        )


class PlanningModelRuntimeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_planning_uses_model_infer_contract(self):
        client = ModelPlanningClient(model_resolver=lambda role: _FakeModel())
        context = PlanningContextPacket(
            original_prompt="Build a tiny operations tool",
            root_objective="Build a tiny operations tool",
            current_node_id="root",
            current_node_path=("root",),
            parent_summary=None,
            sibling_summaries=[],
            dependency_summaries=[],
            requirement_ledger=[],
            constraints=[],
            assumptions=[],
            unresolved_questions=[],
            depth=0,
            budget_remaining={},
        )
        result = await client.generate_structured(
            role="requirements_analyst",
            context=context,
            output_schema=RequirementAnalysis,
            request_id="req-1",
            timeout_seconds=30,
        )
        self.assertIsNone(result.failure_classification)
        self.assertEqual(result.provider, "test-provider")
        self.assertEqual(result.model_id, "test-model")
        self.assertEqual(result.structured_output["root_objective"], "Build a tiny operations tool")

    def test_live_plan_service_has_no_provider_transport_dependency(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "packages/custom_software/plan_service.py").read_text()
        self.assertIn("ModelPlanningClient", source)
        for token in (
            "OllamaPlanningClient",
            "OllamaError",
            "OpenRouterClient",
            "model_client_for_route",
        ):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
