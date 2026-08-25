import os
import unittest
from unittest.mock import AsyncMock, patch

from packages.custom_software import live_planning
from packages.software_projects.planning.provider_planning import (
    ProviderPlanningClient,
    provider_planning_mode,
)
from packages.model_runtime.portfolio import ModelRoute


class ProviderNeutralPlanningTests(unittest.IsolatedAsyncioTestCase):
    def test_legacy_live_planning_names_resolve_to_provider_neutral_runtime(self):
        self.assertIs(live_planning.OllamaPlanningClient, ProviderPlanningClient)
        self.assertIs(live_planning.planning_mode, provider_planning_mode)

    def test_live_mode_accepts_openrouter_without_ollama_key(self):
        fake_client = object()
        with patch.dict(
            os.environ,
            {
                "OPERLY_PLANNING_MODE": "live_llm",
                "OPERLY_MODEL_PROVIDER": "openrouter",
                "OPERLY_MODEL_DEFAULT": "stealth/ox-alpha",
                "OPEN_ROUTER_API": "test-key",
            },
            clear=True,
        ), patch(
            "packages.software_projects.planning.provider_planning.model_client_for_route",
            return_value=fake_client,
        ) as factory:
            mode = provider_planning_mode()

        self.assertEqual(mode, live_planning.PlanningMode.LIVE_LLM)
        route = factory.call_args.args[0]
        self.assertEqual(route.provider, "openrouter")
        self.assertEqual(route.primary, "stealth/ox-alpha")

    async def test_structured_planning_uses_selected_provider_route(self):
        fake_client = AsyncMock()
        fake_client.last_model = "stealth/ox-alpha"
        fake_client.chat.return_value = {
            "role": "assistant",
            "content": '{"root_objective":"Build inventory","requirements":[{"requirement_id":"R-001","source_excerpt":"inventory","normalized_requirement":"Track inventory","category":"inventory","priority":"required","acceptance_criteria":["Inventory can be tracked"]}],"global_exclusions":[],"questions_requiring_user_input":[],"safe_assumptions":[]}',
        }
        context = live_planning.PlanningContextPacket(
            role="requirements_analyst",
            untrusted_requirements={"prompt": "Build inventory"},
        )
        with patch(
            "packages.software_projects.planning.provider_planning.model_route",
            return_value=ModelRoute("openrouter", "stealth/ox-alpha"),
        ), patch(
            "packages.software_projects.planning.provider_planning.model_client_for_route",
            return_value=fake_client,
        ) as factory:
            result = await ProviderPlanningClient().generate_structured(
                role="requirements_analyst",
                context=context,
                output_schema=live_planning.RequirementsAnalysis,
                request_id="req-1",
                timeout_seconds=30,
            )

        self.assertIsNone(result.failure_classification)
        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(result.model_id, "stealth/ox-alpha")
        route = factory.call_args.args[0]
        self.assertEqual(route, ModelRoute("openrouter", "stealth/ox-alpha"))
        fake_client.chat.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
