import os
import unittest
from pathlib import Path
from unittest.mock import patch

from packages.software_projects.coding.model_client import coding_model_client
from packages.model_runtime import model_for_role


class ModelProviderAgnosticTests(unittest.TestCase):
    def test_any_configured_provider_and_model_can_become_a_model_object(self):
        with patch.dict(
            os.environ,
            {
                "OPERLY_MODEL_PROVIDER": "future-provider",
                "OPERLY_MODEL_DEFAULT": "future/model-v9",
            },
            clear=True,
        ):
            model = model_for_role("planner")

        self.assertEqual(model.provider, "future-provider")
        self.assertEqual(model.provider_model_id, "future/model-v9")

    def test_coding_harness_consumes_model_not_provider_route(self):
        with patch.dict(
            os.environ,
            {
                "OPERLY_MODEL_PROVIDER": "future-provider",
                "OPERLY_MODEL_DEFAULT": "future/default",
                "OPERLY_MODEL_CODING_PROVIDER": "another-provider",
                "OPERLY_MODEL_CODING": "future/code-model",
            },
            clear=True,
        ):
            client = coding_model_client("coding")

        model = client.inner.model
        self.assertEqual(model.provider, "another-provider")
        self.assertEqual(model.provider_model_id, "future/code-model")

    def test_coding_boundary_has_no_provider_factory_import(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "packages/coding_harness/model_client.py").read_text()
        self.assertNotIn("model_client_for_route", source)
        self.assertNotIn("OpenRouterClient", source)
        self.assertNotIn("OllamaClient", source)


if __name__ == "__main__":
    unittest.main()
