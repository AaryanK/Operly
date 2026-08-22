import os
import unittest
from unittest.mock import patch

from packages.model_runtime.ollama_client import OllamaClient, _openrouter_messages


class OpenRouterRuntimeTests(unittest.TestCase):
    def test_railway_open_router_secret_activates_openrouter(self):
        with patch.dict(
            os.environ,
            {
                "OPEN_ROUTER_API": "test-openrouter-key",
                "OLLAMA_API_KEY": "legacy-key",
                "OLLAMA_MODEL": "gemma4:31b",
            },
            clear=True,
        ):
            client = OllamaClient()

        self.assertEqual(client.provider, "openrouter")
        self.assertEqual(client.url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(client.model, "google/gemma-4-31b-it:free")
        self.assertEqual(client.api_key, "test-openrouter-key")

    def test_openrouter_model_can_be_overridden_explicitly(self):
        with patch.dict(
            os.environ,
            {
                "OPEN_ROUTER_API": "test-key",
                "OPEN_ROUTER_MODEL": "google/gemma-4-31b-it",
                "OLLAMA_MODEL": "gemma4:31b",
            },
            clear=True,
        ):
            client = OllamaClient()

        self.assertEqual(client.model, "google/gemma-4-31b-it")

    def test_tool_observation_uses_returned_tool_call_id(self):
        messages = [
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
            {"role": "tool", "tool_name": "crm.search", "content": "{\"ok\":true}"},
        ]

        translated = _openrouter_messages(messages)

        self.assertEqual(translated[1]["tool_call_id"], "call_123")
        self.assertNotIn("tool_name", translated[1])

    def test_ollama_remains_default_without_openrouter_secret(self):
        with patch.dict(
            os.environ,
            {
                "OLLAMA_API_KEY": "test-key",
                "OLLAMA_MODEL": "gemma4:31b",
            },
            clear=True,
        ):
            client = OllamaClient()

        self.assertEqual(client.provider, "ollama")
        self.assertEqual(client.model, "gemma4:31b")


if __name__ == "__main__":
    unittest.main()
