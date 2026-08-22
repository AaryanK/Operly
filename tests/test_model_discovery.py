import unittest

from packages.model_runtime.catalog import replace_discovered_resources, select_model_resource
from packages.model_runtime.openrouter_discovery import resource_from_openrouter_model


class ModelDiscoveryTests(unittest.TestCase):
    def test_openrouter_metadata_becomes_provider_agnostic_capabilities(self):
        resource = resource_from_openrouter_model(
            {
                "id": "free/vision-coder:free",
                "name": "Free Vision Coder",
                "description": "A coding and software engineering model.",
                "context_length": 1000000,
                "architecture": {
                    "input_modalities": ["text", "image", "video"],
                    "output_modalities": ["text"],
                },
                "pricing": {"prompt": "0", "completion": "0"},
                "supported_parameters": [
                    "tools",
                    "tool_choice",
                    "reasoning",
                    "structured_outputs",
                ],
            }
        )

        self.assertIsNotNone(resource)
        self.assertTrue(resource.free)
        self.assertEqual(resource.provider, "openrouter")
        self.assertEqual(resource.context_length, 1000000)
        self.assertTrue(
            {"text", "vision", "video", "tools", "reasoning", "coding"}
            <= resource.capabilities
        )

    def test_discovered_models_are_selectable_by_capability(self):
        resource = resource_from_openrouter_model(
            {
                "id": "free/translation:free",
                "name": "Translation Specialist",
                "description": "Translation model",
                "architecture": {
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                },
                "pricing": {"prompt": "0", "completion": "0"},
                "supported_parameters": [],
            }
        )
        self.assertIsNotNone(resource)
        replace_discovered_resources("openrouter", [resource])

        selected = select_model_resource(
            "translation",
            exclude=("openrouter", "stealth/ox-alpha"),
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, "free/translation:free")


if __name__ == "__main__":
    unittest.main()
