import os
import unittest
from unittest.mock import patch

from packages.model_runtime import (
    InferenceBudget,
    InferenceRequest,
    InferenceResult,
    ModelInferenceError,
    model_for_role,
)
from packages.model_runtime.portfolio import model_route
from packages.model_runtime.registry import ModelPool


class _FailingModel:
    def __init__(self, model_id, classification="response_timeout"):
        self.id = model_id
        self.tags = frozenset({"coding"})
        self.capabilities = frozenset({"text", "tools", "coding"})
        self.traits = object()
        self.classification = classification
        self.calls = 0

    async def infer(self, request):
        self.calls += 1
        raise ModelInferenceError(
            f"{self.id} failed",
            classification=self.classification,
            retryable=self.classification != "invalid_request",
            provider="provider-a",
            model_id=self.id,
        )


class _SuccessModel:
    def __init__(self, model_id):
        self.id = model_id
        self.tags = frozenset({"coding"})
        self.capabilities = frozenset({"text", "tools", "coding"})
        self.traits = object()
        self.calls = 0

    async def infer(self, request):
        self.calls += 1
        return InferenceResult(
            message={"role": "assistant", "content": "ok"},
            model_resource_id=self.id,
            provider="provider-b",
            provider_model_id=self.id,
            latency_ms=1,
            usage=None,
            finish_reason="stop",
        )


class ModelPoolFailoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_falls_through_to_next_model(self):
        first = _FailingModel("primary")
        second = _SuccessModel("fallback")
        pool = ModelPool([first, second], id="coding")

        result = await pool.infer(
            InferenceRequest(
                messages=({"role": "user", "content": "edit source"},),
                budget=InferenceBudget(timeout_seconds=1, max_models=2),
            )
        )

        self.assertEqual(result.provider_model_id, "fallback")
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)

    def test_default_openrouter_coding_route_has_provider_local_fallbacks(self):
        with patch.dict(os.environ, {}, clear=True):
            route = model_route("coding")

        self.assertEqual(route.provider, "openrouter")
        self.assertEqual(route.primary, "stealth/ox-alpha")
        self.assertEqual(
            route.fallbacks,
            ("qwen/qwen3-coder-flash", "openai/gpt-oss-120b:free"),
        )

    def test_default_openrouter_fallback_ids_do_not_leak_to_other_provider(self):
        with patch.dict(
            os.environ,
            {
                "OPERLY_MODEL_PROVIDER": "ollama",
                "OPERLY_MODEL_DEFAULT": "gemma4:31b",
            },
            clear=True,
        ):
            route = model_route("coding")

        self.assertEqual(route.provider, "ollama")
        self.assertEqual(route.primary, "gemma4:31b")
        self.assertEqual(route.fallbacks, ())

    def test_role_candidate_configuration_builds_multi_model_pool(self):
        candidates = (
            '[{"provider":"openrouter","model":"stealth/ox-alpha"},'
            '{"provider":"openrouter","model":"qwen/qwen3-coder-flash"},'
            '{"provider":"openrouter","model":"openai/gpt-oss-120b:free"}]'
        )
        with patch.dict(
            os.environ,
            {"OPERLY_MODEL_CODING_CANDIDATES_JSON": candidates},
            clear=False,
        ):
            model = model_for_role("coding")

        self.assertIsInstance(model, ModelPool)
        self.assertEqual(len(model.models), 3)
        self.assertEqual(model.models[0].provider_model_id, "stealth/ox-alpha")
        self.assertEqual(model.models[1].provider_model_id, "qwen/qwen3-coder-flash")


if __name__ == "__main__":
    unittest.main()
