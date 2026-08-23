import json
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
from packages.model_runtime.ollama_client import OllamaError
from packages.model_runtime.openrouter_client import OpenRouterClient
from packages.model_runtime.portfolio import model_route
from packages.model_runtime.registry import ConfiguredModel, ModelPool, _failure


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
            retryable=self.classification not in {"invalid_request", "quota_or_credits"},
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


class _FakeResponse:
    status = 200
    headers = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        )


class _FakeSession:
    def __init__(self):
        self.payload = None

    def post(self, url, *, headers, json):
        self.payload = json
        return _FakeResponse()


class _BudgetAwareClient:
    def __init__(self):
        self.max_attempts = 3
        self.fallback_models = ["legacy"]
        self.fallback_model = "legacy"
        self.max_tokens = 65_536
        self.last_model = "example/model"

    async def chat(self, messages, tools=None):
        return {"role": "assistant", "content": "ok"}


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

    async def test_credit_exhaustion_falls_through_to_next_model(self):
        first = _FailingModel("paid-or-constrained", classification="quota_or_credits")
        second = _SuccessModel("available-fallback")
        pool = ModelPool([first, second], id="coding")

        result = await pool.infer(
            InferenceRequest(messages=({"role": "user", "content": "edit source"},))
        )

        self.assertEqual(result.provider_model_id, "available-fallback")
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)

    def test_http_402_is_not_classified_as_bad_request(self):
        error = _failure(
            OllamaError("credits exhausted", status=402, retryable=False),
            provider="openrouter",
            model_id="example/model",
        )

        self.assertEqual(error.classification, "quota_or_credits")
        self.assertFalse(error.retryable)

    async def test_provider_neutral_budget_reaches_adapter_output_limit(self):
        client = _BudgetAwareClient()
        model = ConfiguredModel(
            resource_id="test-model",
            provider="future-provider",
            provider_model_id="example/model",
            capabilities={"text", "tools"},
        )
        with patch(
            "packages.model_runtime.registry.model_client_for_route",
            return_value=client,
        ):
            await model.infer(
                InferenceRequest(
                    messages=({"role": "user", "content": "edit source"},),
                    budget=InferenceBudget(max_output_tokens=8192),
                )
            )

        self.assertEqual(client.max_tokens, 8192)
        self.assertEqual(client.max_attempts, 1)
        self.assertEqual(client.fallback_models, [])

    async def test_openrouter_agent_turn_has_bounded_output_reservation(self):
        with patch.dict(
            os.environ,
            {"OPEN_ROUTER_API": "test-key"},
            clear=False,
        ):
            client = OpenRouterClient(model="stealth/ox-alpha")
            session = _FakeSession()
            result = await client._request_once(
                session,
                {},
                "stealth/ox-alpha",
                [{"role": "user", "content": "edit the source"}],
                [],
            )

        self.assertEqual(result["content"], "ok")
        self.assertEqual(session.payload["max_tokens"], 16_384)

    async def test_openrouter_output_reservation_is_operator_configurable(self):
        with patch.dict(
            os.environ,
            {
                "OPEN_ROUTER_API": "test-key",
                "OPEN_ROUTER_MAX_TOKENS": "4096",
            },
            clear=False,
        ):
            client = OpenRouterClient(model="stealth/ox-alpha")
            session = _FakeSession()
            await client._request_once(
                session,
                {},
                "stealth/ox-alpha",
                [{"role": "user", "content": "edit the source"}],
                [],
            )

        self.assertEqual(session.payload["max_tokens"], 4096)

    def test_default_openrouter_coding_route_has_provider_local_fallbacks(self):
        with patch.dict(os.environ, {}, clear=True):
            route = model_route("coding")

        self.assertEqual(route.provider, "openrouter")
        self.assertEqual(route.primary, "stealth/ox-alpha")
        self.assertEqual(
            route.fallbacks,
            ("openai/gpt-oss-120b:free", "qwen/qwen3-coder-flash"),
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
            '{"provider":"openrouter","model":"openai/gpt-oss-120b:free"},'
            '{"provider":"openrouter","model":"qwen/qwen3-coder-flash"}]'
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
        self.assertEqual(model.models[1].provider_model_id, "openai/gpt-oss-120b:free")


if __name__ == "__main__":
    unittest.main()
