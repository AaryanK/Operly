import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from packages.model_runtime.catalog import model_resources, select_model_resource
from packages.model_runtime.portfolio import ModelRoute
from packages.model_runtime.service import ModelInvocationService


class ModelCatalogTests(unittest.IsolatedAsyncioTestCase):
    def test_ox_is_default_orchestrator_resource(self):
        with patch.dict(os.environ, {}, clear=True):
            resources = model_resources()

        self.assertEqual(resources[0].provider, "openrouter")
        self.assertEqual(resources[0].id, "stealth/ox-alpha")
        self.assertIn("reasoning", resources[0].capabilities)

    def test_catalog_can_add_specialists_without_harness_code_changes(self):
        catalog = json.dumps(
            [
                {
                    "provider": "openrouter",
                    "id": "free/vision-specialist",
                    "capabilities": ["vision"],
                    "free": True,
                    "priority": 10,
                },
                {
                    "provider": "ollama",
                    "id": "local/vision-specialist",
                    "capabilities": ["vision"],
                    "free": True,
                    "priority": 20,
                },
            ]
        )
        with patch.dict(
            os.environ,
            {"OPERLY_MODEL_CATALOG_JSON": catalog},
            clear=True,
        ):
            selected = select_model_resource(
                "vision",
                exclude=("openrouter", "stealth/ox-alpha"),
            )

        self.assertIsNotNone(selected)
        self.assertEqual(selected.provider, "openrouter")
        self.assertEqual(selected.id, "free/vision-specialist")

    async def test_delegated_model_receives_no_tools(self):
        catalog = json.dumps(
            [
                {
                    "provider": "openrouter",
                    "id": "free/reasoner",
                    "capabilities": ["reasoning"],
                    "free": True,
                }
            ]
        )
        fake_client = AsyncMock()
        fake_client.chat.return_value = {"role": "assistant", "content": "specialist result"}

        with patch.dict(
            os.environ,
            {"OPERLY_MODEL_CATALOG_JSON": catalog},
            clear=True,
        ), patch(
            "packages.model_runtime.service.model_client_for_route",
            return_value=fake_client,
        ) as factory:
            result = await ModelInvocationService().invoke(
                capability="reasoning",
                objective="Critique this plan",
            )

        route = factory.call_args.args[0]
        self.assertEqual(route, ModelRoute("openrouter", "free/reasoner"))
        self.assertEqual(result.content, "specialist result")
        self.assertEqual(fake_client.chat.await_args.args[1], [])


if __name__ == "__main__":
    unittest.main()
