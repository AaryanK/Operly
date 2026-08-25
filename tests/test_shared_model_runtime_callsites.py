import json
import unittest
from unittest.mock import patch

from packages.model_runtime.semantic_router import SemanticRouter
from packages.software_projects.planning.live_planning import PlanningContextPacket, RequirementsAnalysis
from packages.software_projects.planning.model_planning_client import ModelPlanningClient
from packages.model_runtime import InferenceResult


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, messages, tools):
        self.calls += 1
        return {"content": self.responses.pop(0)}


class FakePlanningModel:
    id = "planner-fixture"

    async def infer(self, request):
        return InferenceResult(
            message={
                "role": "assistant",
                "content": (
                    '{"root_objective":"Build a veterinary appointment system",'
                    '"requirements":[{"requirement_id":"R-001",'
                    '"source_excerpt":"veterinary appointment system",'
                    '"normalized_requirement":"Provide veterinary appointments",'
                    '"category":"product","priority":"mandatory",'
                    '"acceptance_criteria":["Appointments can be managed"]}],'
                    '"global_exclusions":[],"questions_requiring_user_input":[],"safe_assumptions":[]}'
                ),
            },
            model_resource_id="planner-fixture",
            provider="test-provider",
            provider_model_id="planner-model",
            latency_ms=1,
            usage=None,
            finish_reason="stop",
        )


class SharedModelRuntimeCallsiteTests(unittest.IsolatedAsyncioTestCase):
    async def test_software_planning_uses_shared_model_infer_contract(self):
        client = ModelPlanningClient(model_resolver=lambda role: FakePlanningModel())
        result = await client.generate_structured(
            role="requirements_analyst",
            context=PlanningContextPacket(
                role="requirements_analyst",
                untrusted_requirements={"prompt": "Build a veterinary appointment system"},
                current_contract={},
                related_contracts={},
                constraints={},
                previous_findings=[],
                budget={"remaining_calls": 5},
            ),
            output_schema=RequirementsAnalysis,
            request_id="shared-runtime-1",
            timeout_seconds=30,
        )
        self.assertIsNone(result.failure_classification)
        self.assertEqual(result.provider, "test-provider")
        self.assertEqual(result.model_id, "planner-model")
        self.assertEqual(result.structured_output["root_objective"], "Build a veterinary appointment system")

    async def test_semantic_router_resolves_bounded_task_role(self):
        client = FakeClient(
            [
                json.dumps(
                    {
                        "domainMatch": True,
                        "known": True,
                        "route": "software_build",
                        "reason": "The bounded capability fully satisfies the request.",
                    }
                )
            ]
        )
        with patch(
            "packages.model_runtime.semantic_router.model_chat_client_for_role",
            return_value=client,
        ) as factory:
            decision = await SemanticRouter().decide(
                request="Build a secure staff portal.",
                domain="software operations",
                routes={"software_build": "build a governed software project"},
            )
        factory.assert_called_once_with("bounded_task")
        self.assertTrue(decision.known)
        self.assertEqual(decision.route_id, "software_build")


if __name__ == "__main__":
    unittest.main()
