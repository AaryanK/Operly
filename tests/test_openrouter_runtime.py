import os
import unittest
from unittest.mock import patch

from packages.model_runtime.ollama_client import OllamaClient
from packages.model_runtime.openrouter_client import OpenRouterClient, _openrouter_messages
from packages.model_runtime.portfolio import ModelRoute, model_route
from packages.model_runtime.providers import (
    installed_model_providers,
    model_client_for_route,
    register_model_provider,
)


class OpenRouterRuntimeTests(unittest.TestCase):
    def test_railway_open_router_secret_builds_openrouter_plugin(self):
        with patch.dict(
            os.environ,
            {"OPEN_ROUTER_API": "test-openrouter-key"},
            clear=True,
        ):
            client = model_client_for_route(
                ModelRoute("openrouter", "openai/gpt-oss-120b:free")
            )

        self.assertIsInstance(client, OpenRouterClient)
        self.assertEqual(client.url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(client.model, "openai/gpt-oss-120b:free")
        self.assertEqual(client.api_key, "test-openrouter-key")

    def test_all_default_roles_use_free_gpt_oss_through_openrouter(self):
        with patch.dict(os.environ, {}, clear=True):
            for role in (
                "requirements_analyst",
                "planner",
                "global_validator",
                "coding",
                "repair",
                "capability_placement",
                "business_agent",
                "bounded_task",
            ):
                route = model_route(role)
                self.assertEqual(route.provider, "openrouter")
                self.assertEqual(route.primary, "openai/gpt-oss-120b:free")

    def test_global_model_switch_is_env_only(self):
        with patch.dict(
            os.environ,
            {
                "OPERLY_MODEL_PROVIDER": "ollama",
                "OPERLY_MODEL_DEFAULT": "replacement-model",
            },
            clear=True,
        ):
            route = model_route("planner")

        self.assertEqual(route.provider, "ollama")
        self.assertEqual(route.primary, "replacement-model")

    def test_explicit_ollama_route_does_not_switch_because_openrouter_key_exists(self):
        with patch.dict(
            os.environ,
            {
                "OPEN_ROUTER_API": "openrouter-key",
                "OLLAMA_API_KEY": "ollama-key",
            },
            clear=True,
        ):
            client = model_client_for_route(ModelRoute("ollama", "gemma4:31b"))

        self.assertIsInstance(client, OllamaClient)
        self.assertEqual(client.model, "gemma4:31b")
        self.assertEqual(client.api_key, "ollama-key")

    def test_tool_observation_recovers_provider_call_id(self):
        translated = _openrouter_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {"name": "crm.search", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_name": "crm.search",
                    "content": "{\"ok\":true}",
                },
            ]
        )

        self.assertEqual(translated[1]["tool_call_id"], "call_123")
        self.assertNotIn("tool_name", translated[1])

    def test_image_translation_is_provider_adapter_concern(self):
        translated = _openrouter_messages(
            [{"role": "user", "content": "inspect", "images": ["abc123"]}]
        )

        self.assertEqual(
            translated[0]["content"][0], {"type": "text", "text": "inspect"}
        )
        self.assertTrue(
            translated[0]["content"][1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            )
        )

    def test_provider_registry_is_extensible_without_harness_changes(self):
        class StubClient:
            last_model = "stub"

            async def chat(self, messages, tools=None):
                return {"role": "assistant", "content": "ok"}

        register_model_provider(
            "unit-test-provider", lambda route: StubClient(), replace=True
        )
        client = model_client_for_route(ModelRoute("unit-test-provider", "anything"))

        self.assertIsInstance(client, StubClient)
        self.assertIn("unit-test-provider", installed_model_providers())


if __name__ == "__main__":
    unittest.main()
