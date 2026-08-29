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
from packages.model_runtime.registry import AdaptiveRoleModel


class _FakeResponse:
    status = 200
    headers = {}

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


class _RequestTooLargeResponse:
    status = 413
    headers = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return json.dumps(
            {
                "error": {
                    "message": (
                        "Request too large for model on tokens per minute: "
                        "Limit 8000, Requested 18606"
                    )
                }
            }
        )


class _ContextTooLargeResponse:
    status = 413
    headers = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return json.dumps(
            {
                "error": {
                    "message": (
                        "Request exceeds the maximum context length for this model: "
                        "maximum 8192 tokens, requested 12000"
                    )
                }
            }
        )


class _FakeSession:
    def __init__(self, response=None):
        self.url = None
        self.headers = None
        self.payload = None
        self.response = response or _FakeResponse()

    def post(self, url, *, headers, json):
        self.url = url
        self.headers = headers
        self.payload = json
        return self.response


class _FailingModel:
    def __init__(
        self,
        model_id,
        *,
        provider="provider-a",
        classification="response_timeout",
        retryable=True,
    ):
        self.id = model_id
        self.provider = provider
        self.tags = frozenset({"text"})
        self.capabilities = frozenset({"text"})
        self.traits = object()
        self.calls = 0
        self.classification = classification
        self.retryable = retryable

    async def infer(self, request):
        self.calls += 1
        raise ModelInferenceError(
            f"{self.id} failed",
            classification=self.classification,
            retryable=self.retryable,
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
        # Synthetic models in the failover tests are intentionally provider-neutral.
        # Mock only the scorer eligibility boundary; real provider/zero-cost policy
        # remains covered by its dedicated tests and untouched in production.
        self.route_policy_patch = patch(
            "packages.model_runtime.scoring.route_is_zero_cost",
            return_value=True,
        )
        self.route_policy_patch.start()
        self.addCleanup(self.route_policy_patch.stop)
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

    async def test_openai_compatible_tpm_413_becomes_provider_capacity_failure(self):
        with patch.dict(os.environ, {"groq_api_key": "test-key"}, clear=True):
            client = OpenAICompatibleClient(
                provider="groq",
                model="openai/gpt-oss-20b",
                default_url="https://api.groq.com/openai/v1/chat/completions",
                api_key_envs=("GROQ_API_KEY", "groq_api_key"),
                env_prefix="GROQ",
            )
            session = _FakeSession(_RequestTooLargeResponse())
            with self.assertRaises(ModelInferenceError) as caught:
                await client._request_once(
                    session,
                    {"Authorization": "Bearer test-key"},
                    "openai/gpt-oss-20b",
                    [{"role": "user", "content": "large packet"}],
                    [],
                )

        self.assertEqual(caught.exception.classification, "rate_limited")
        # Do not retry the same route immediately. ModelPool may continue to another
        # eligible route, including a separate route on the same provider.
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(caught.exception.provider, "groq")

    async def test_openai_compatible_true_context_413_stays_route_specific(self):
        with patch.dict(os.environ, {"groq_api_key": "test-key"}, clear=True):
            client = OpenAICompatibleClient(
                provider="groq",
                model="openai/gpt-oss-20b",
                default_url="https://api.groq.com/openai/v1/chat/completions",
                api_key_envs=("GROQ_API_KEY", "groq_api_key"),
                env_prefix="GROQ",
            )
            session = _FakeSession(_ContextTooLargeResponse())
            with self.assertRaises(ModelInferenceError) as caught:
                await client._request_once(
                    session,
                    {"Authorization": "Bearer test-key"},
                    "openai/gpt-oss-20b",
                    [{"role": "user", "content": "large packet"}],
                    [],
                )

        self.assertEqual(caught.exception.classification, "request_too_large")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(caught.exception.provider, "groq")

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

    def test_automatic_bounded_task_role_uses_adaptive_portfolio(self):
        with patch.dict(os.environ, self.provider_env, clear=True):
            model = model_for_role("bounded_task")

        self.assertIsInstance(model, AdaptiveRoleModel)
        self.assertEqual(model.role, "bounded_task")
        self.assertIn("tools", model.capabilities)

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

    async def test_request_too_large_falls_through_to_next_model(self):
        too_small = _FailingModel(
            "small-context-route",
            classification="request_too_large",
            retryable=False,
        )
        larger_route = _SuccessModel("larger-route")
        pool = ModelPool([too_small, larger_route], id="size-fallback")

        result = await pool.infer(
            InferenceRequest(messages=({"role": "user", "content": "large work"},))
        )

        self.assertEqual(result.provider_model_id, "larger-route")
        self.assertEqual(too_small.calls, 1)
        self.assertEqual(larger_route.calls, 1)

    async def test_route_rate_limit_can_try_other_route_on_same_provider(self):
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

        self.assertEqual(result.provider, "provider-a")
        self.assertEqual(first.calls, 1)
        self.assertEqual(same_provider.calls, 1)
        self.assertEqual(other_provider.calls, 0)


if __name__ == "__main__":
    unittest.main()
