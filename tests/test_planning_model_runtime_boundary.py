import unittest
from pathlib import Path

from packages.software_projects.planning.live_planning import PlanningContextPacket, RequirementsAnalysis
from packages.software_projects.planning.model_planning_client import ModelPlanningClient
from packages.model_runtime import InferenceResult


class _FakeModel:
    id = "fake-planner"

    async def infer(self, request):
        return InferenceResult(
            message={
                "role": "assistant",
                "content": (
                    '{"root_objective":"Build a tiny operations tool","requirements":['
                    '{"requirement_id":"R-001","source_excerpt":"Build a tiny operations tool",'
                    '"normalized_requirement":"Provide a tiny operations tool",'
                    '"category":"product","priority":"mandatory",'
                    '"acceptance_criteria":["The operations tool is available"]}],'
                    '"global_exclusions":[],"questions_requiring_user_input":[],"safe_assumptions":[]}'
                ),
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
            role="requirements_analyst",
            untrusted_requirements={"prompt": "Build a tiny operations tool"},
            current_contract={},
            related_contracts={},
            constraints={},
            previous_findings=[],
            budget={"remaining_calls": 10},
        )
        result = await client.generate_structured(
            role="requirements_analyst",
            context=context,
            output_schema=RequirementsAnalysis,
            request_id="req-1",
            timeout_seconds=30,
        )
        self.assertIsNone(result.failure_classification)
        self.assertEqual(result.provider, "test-provider")
        self.assertEqual(result.model_id, "test-model")
        self.assertEqual(result.structured_output["root_objective"], "Build a tiny operations tool")
        self.assertEqual(result.structured_output["requirements"][0]["requirement_id"], "R-001")

    def test_live_plan_service_has_no_provider_transport_dependency(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "packages/software_projects/planning/plan_service.py").read_text()
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
