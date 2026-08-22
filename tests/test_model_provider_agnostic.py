import os
import unittest
from unittest.mock import patch

from packages.coding_harness.model_client import coding_model_client
from packages.model_runtime.portfolio import ModelRoute, model_route


class ModelProviderAgnosticTests(unittest.TestCase):
    def test_any_configured_provider_and_model_can_route(self):
        with patch.dict(
            os.environ,
            {
                "OPERLY_MODEL_PROVIDER": "future-provider",
                "OPERLY_MODEL_DEFAULT": "future/model-v9",
            },
            clear=True,
        ):
            route = model_route("planner")

        self.assertEqual(route, ModelRoute("future-provider", "future/model-v9"))

    def test_coding_harness_has_no_model_or_provider_allowlist(self):
        stub_client = object()
        with patch.dict(
            os.environ,
            {
                "OPERLY_MODEL_CODING_PROVIDER": "future-provider",
                "OPERLY_MODEL_CODING": "future/code-model",
            },
            clear=True,
        ), patch(
            "packages.coding_harness.model_client.model_client_for_route",
            return_value=stub_client,
        ) as factory:
            client = coding_model_client("coding")

        route = factory.call_args.args[0]
        self.assertEqual(route, ModelRoute("future-provider", "future/code-model"))
        self.assertIs(client.inner, stub_client)


if __name__ == "__main__":
    unittest.main()
