import json
import os
import unittest
from unittest.mock import patch

from packages.model_runtime import (
    InferenceRequest,
    InferenceResult,
    ModelInferenceError,
    ModelPool,
    installed_model_providers,
    model_for_role,
    model_resources,
)
from packages.model_runtime.openai_compatible_client import OpenAICompatibleClient


class _FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "ok",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            }
        )


class _FakeSession:
    def __init__(self):
        self.url = None
        self.headers = None
        self.payload = None

    def post(self, url, *, headers, json):
        self.url = url
        self.headers = headers
        self.payload = json
        return _FakeResponse()


class _FailingModel:
    def __init__(self, model_id, *, provider="provider-a", classification="response_timeout"):
        self.id = model_id
        self.provider = provider
        self.tags = frozenset({"text"})
        self.capabilities = frozenset({"text"})
        self.traits = object()
        self.calls = 0
        self.classification = classification

    async def infer(self, request):
        self.calls += 1
        raise ModelInferenceError(
            f"{self.id} failed",
            classification=self.classification,
            retryable=True,
            provider=self.provider,
            model_id=self.id,
        )


class _SuccessModel:
    def __init__(self, model_id, *, provider="provider-b"):
        self.id = model_id
        self.provider = provider
        self.tags = frozenset({"text"})
        self.capabilities = frozenset({"text"})
        self.traits = object()
        self.calls = 0

    async def infer(self, request):
        self.calls += 1
        return InferenceResult(
            message={"role": "assistant", "content": "ok"},
            model_resource_id=self.id,
            provider=self.provider,
            provider_model_id=self.id,
            latency_ms=1,
        )


class MultiProviderModelPortfolioTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider_env = {
            "OPEN_ROUTER_API": "test-openrouter",
            "OLLAMA_API_KEY": "test-ollama",
            "groq_api_key": "test-groq",
            "gemini_api_key": "test-gemini",
            "nvidia_api_key": "test-nvidia",
            "OPERLY_MODEL_AUTO_PORTFOLIO": "1",
        }

    def test_all_five_provider_adapters_are_installed(self):
        self.assertEqual(
            set(installed_model_providers()),
            {"openrouter", "ollama", "groq", "gemini", "nvidia"},
        )

    async def test_openai_compatible_adapter_accepts_lowercase_key_and_preserves_usage(self):
        with patch.dict(os.environ, {"groq_api_key": "test-key"}, clear=True):
            client = OpenAICompatibleClient(
                provider="groq",
                model="openai/gpt-oss-20b",
                default_url="https://api.groq.com/openai/v1/chat/completions",
                api_key_envs=("GROQ_API_KEY", "groq_api_key"),
                env_prefix="GROQ",
            )
            session = _FakeSession()
            result = await client._request_once(
                session,
                {"Authorization": "Bearer test-key"},
                "openai/gpt-oss-20b",
                [{"role": "user", "content": "hello"}],
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "ping",
                            "description": "Ping",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
            )

        self.assertEqual(session.payload["model"], "openai/gpt-oss-20b")
        self.assertEqual(session.payload["tools"][0]["function"]["name"], "ping")
        self.assertEqual(result["usage"]["total_tokens"], 12)
        self.assertEqual(result["finish_reason"], "stop")

    def test_lowercase_railway_keys_activate_cards_for_all_five_providers(self):
        with patch.dict(os.environ, self.provider_env, clear=True):
            cards = model_resources()

        providers = {card.provider for card in cards}
        self.assertEqual(
            providers,
            {"openrouter", "ollama", "groq", "gemini", "nvidia"},
        )
        self.assertIn(
            ("groq", "openai/gpt-oss-120b"),
            {(card.provider, card.id) for card in cards},
        )
        self.assertIn(
            ("gemini", "gemini-3.6-flash"),
            {(card.provider, card.id) for card in cards},
        )
        self.assertIn(
            ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
            {(card.provider, card.id) for card in cards},
        )

    def test_same_nemotron_ultra_has_redundant_direct_and_openrouter_routes(self):
        with patch.dict(os.environ, self.provider_env, clear=True):
            cards = {
                (card.provider, card.id): card
                for card in model_resources()
            }

        direct = cards[("nvidia", "nvidia/nemotron-3-ultra-550b-a55b")]
        routed = cards[
            ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free")
        ]
        self.assertEqual(direct.canonical_id, routed.canonical_id)
        self.assertEqual(direct.verified_latency_ms, 536)
        self.assertEqual(routed.verified_latency_ms, 1044)
        self.assertEqual(direct.usage_cost_label(), "Free tier / quota")
        self.assertEqual(routed.usage_cost_label(), "$0 route")
        self.assertEqual(routed.input_cost_per_million, 0.0)
        self.assertEqual(routed.output_cost_per_million, 0.0)

    def test_automatic_bounded_task_pool_is_provider_diverse(self):
        with patch.dict(os.environ, self.provider_env, clear=True):
            model = model_for_role("bounded_task")

        self.assertIsInstance(model, ModelPool)
        self.assertEqual(len(model.models), 5)
        self.assertEqual(
            {candidate.provider for candidate in model.models},
            {"openrouter", "ollama", "groq", "gemini", "nvidia"},
        )

    async def test_successful_fallback_becomes_sticky_across_agent_turns(self):
        primary = _FailingModel("slow-primary")
        fallback = _SuccessModel("fast-fallback")
        pool = ModelPool([primary, fallback], id="sticky")
        request = InferenceRequest(
            messages=({"role": "user", "content": "work"},)
        )

        first = await pool.infer(request)
        second = await pool.infer(request)

        self.assertEqual(first.provider_model_id, "fast-fallback")
        self.assertEqual(second.provider_model_id, "fast-fallback")
        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 2)

    async def test_provider_rate_limit_skips_other_routes_on_same_provider(self):
        first = _FailingModel(
            "provider-a-primary",
            provider="provider-a",
            classification="rate_limited",
        )
        same_provider = _SuccessModel(
            "provider-a-secondary",
            provider="provider-a",
        )
        other_provider = _SuccessModel(
            "provider-b-primary",
            provider="provider-b",
        )
        pool = ModelPool(
            [first, same_provider, other_provider],
            id="provider-circuit-breaker",
        )

        result = await pool.infer(
            InferenceRequest(messages=({"role": "user", "content": "work"},))
        )

        self.assertEqual(result.provider, "provider-b")
        self.assertEqual(first.calls, 1)
        self.assertEqual(same_provider.calls, 0)
        self.assertEqual(other_provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
