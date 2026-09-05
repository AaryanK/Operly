from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from packages.agent_runtime.inference import (
    AgentInferenceError,
    AgentInferenceRuntime,
    InferenceBudget,
    InferencePortfolio,
    InferenceRequest,
    InferenceRoute,
    InferenceTransportResult,
)


class FakeTransport:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if not self.outcomes:
            raise AssertionError("unexpected inference transport call")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def route(
    provider: str,
    *,
    attempts: int = 1,
    input_cost: float | None = None,
    output_cost: float | None = None,
) -> InferenceRoute:
    fixed = {
        "groq": "https://api.groq.com/openai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    }
    return InferenceRoute(
        provider=provider,
        base_url=fixed[provider],
        api_key="test-key",  # pragma: allowlist secret
        model_id=f"{provider}-model",
        max_attempts=attempts,
        input_cost_per_million=input_cost,
        output_cost_per_million=output_cost,
    )


class KernelV3InferenceTests(unittest.IsolatedAsyncioTestCase):
    def test_environment_cannot_override_fixed_provider_destination(self):
        with patch.dict(
            os.environ,
            {
                "OPERLY_AGENT_MODEL_PROVIDER": "groq",
                "GROQ_API_KEY": "test-key",  # pragma: allowlist secret
                "OPERLY_AGENT_MODEL_BASE_URL": "https://attacker.invalid/v1",
            },
            clear=True,
        ):
            selected = InferenceRoute.from_environment()
        self.assertEqual(selected.base_url, "https://api.groq.com/openai/v1")
        self.assertNotIn("attacker", selected.base_url)

    def test_fallbacks_are_explicit_and_fixed(self):
        with patch.dict(
            os.environ,
            {
                "OPERLY_AGENT_MODEL_PROVIDER": "groq",
                "GROQ_API_KEY": "groq-test",  # pragma: allowlist secret
                "OPERLY_AGENT_MODEL_FALLBACK_PROVIDERS": "openrouter,gemini",
                "OPENROUTER_API_KEY": "or-test",  # pragma: allowlist secret
                "GEMINI_API_KEY": "gemini-test",  # pragma: allowlist secret
            },
            clear=True,
        ):
            portfolio = InferencePortfolio.from_environment()
        self.assertEqual([item.provider for item in portfolio.routes], ["groq", "openrouter", "gemini"])
        self.assertEqual(portfolio.routes[1].base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(portfolio.routes[2].base_url, "https://generativelanguage.googleapis.com/v1beta/openai")

    def test_explicit_unconfigured_fallback_fails_configuration(self):
        with patch.dict(
            os.environ,
            {
                "OPERLY_AGENT_MODEL_PROVIDER": "groq",
                "GROQ_API_KEY": "groq-test",  # pragma: allowlist secret
                "OPERLY_AGENT_MODEL_FALLBACK_PROVIDERS": "openrouter",
            },
            clear=True,
        ):
            with self.assertRaises(AgentInferenceError) as caught:
                InferencePortfolio.from_environment()
        self.assertEqual(caught.exception.code, "inference_not_configured")

    async def test_retryable_primary_failure_can_fail_over_to_explicit_next_route(self):
        transport = FakeTransport(
            [
                AgentInferenceError(
                    "primary unavailable",
                    code="inference_provider_unavailable",
                    retryable=True,
                ),
                InferenceTransportResult(content="fallback answer", usage={"total_tokens": 8}),
            ]
        )
        runtime = AgentInferenceRuntime(
            portfolio=InferencePortfolio(routes=(route("groq"), route("openrouter"))),
            budget=InferenceBudget(max_total_attempts=2, max_provider_routes=2),
            transport=transport,
        )
        result = await runtime.complete(InferenceRequest(system="system", user_payload="hello"))
        self.assertEqual(result.content, "fallback answer")
        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(result.attempts, 2)
        self.assertEqual([call["route"].provider for call in transport.calls], ["groq", "openrouter"])

    async def test_nonretryable_failure_is_not_sprayed_across_providers(self):
        transport = FakeTransport(
            [
                AgentInferenceError(
                    "bad configured credential",
                    code="inference_provider_auth",
                    retryable=False,
                ),
                InferenceTransportResult(content="must not run"),
            ]
        )
        runtime = AgentInferenceRuntime(
            portfolio=InferencePortfolio(routes=(route("groq"), route("openrouter"))),
            budget=InferenceBudget(max_total_attempts=3, max_provider_routes=2),
            transport=transport,
        )
        with self.assertRaises(AgentInferenceError) as caught:
            await runtime.complete(InferenceRequest(system="system", user_payload="hello"))
        self.assertEqual(caught.exception.code, "inference_provider_auth")
        self.assertEqual(len(transport.calls), 1)

    async def test_global_attempt_budget_stops_before_another_provider(self):
        retryable = lambda: AgentInferenceError(  # noqa: E731
            "temporary failure",
            code="inference_provider_unavailable",
            retryable=True,
        )
        transport = FakeTransport([retryable(), retryable(), InferenceTransportResult(content="too late")])
        runtime = AgentInferenceRuntime(
            portfolio=InferencePortfolio(
                routes=(route("groq", attempts=3), route("openrouter", attempts=3))
            ),
            budget=InferenceBudget(max_total_attempts=2, max_provider_routes=2),
            transport=transport,
        )
        with self.assertRaises(AgentInferenceError) as caught:
            await runtime.complete(InferenceRequest(system="system", user_payload="hello"))
        self.assertEqual(caught.exception.code, "inference_attempt_budget_exhausted")
        self.assertEqual(len(transport.calls), 2)
        self.assertTrue(all(call["route"].provider == "groq" for call in transport.calls))

    async def test_structured_json_mode_fallback_counts_as_real_attempt(self):
        transport = FakeTransport(
            [
                AgentInferenceError(
                    "json mode unsupported",
                    code="inference_json_mode_unsupported",
                    retryable=True,
                ),
                InferenceTransportResult(content='{"ok":true}'),
            ]
        )
        runtime = AgentInferenceRuntime(
            portfolio=InferencePortfolio(routes=(route("groq", attempts=2),)),
            budget=InferenceBudget(max_total_attempts=2, max_provider_routes=1),
            transport=transport,
        )
        result = await runtime.complete(
            InferenceRequest(system="system", user_payload={"request": "x"}, structured=True)
        )
        self.assertEqual(result.attempts, 2)
        self.assertEqual(
            [call["include_response_format"] for call in transport.calls],
            [True, False],
        )

    async def test_request_byte_budget_fails_before_transport(self):
        transport = FakeTransport([InferenceTransportResult(content="must not run")])
        runtime = AgentInferenceRuntime(
            portfolio=InferencePortfolio(routes=(route("groq"),)),
            budget=InferenceBudget(max_request_bytes=64, max_total_attempts=1),
            transport=transport,
        )
        with self.assertRaises(AgentInferenceError) as caught:
            await runtime.complete(
                InferenceRequest(system="system", user_payload="x" * 500)
            )
        self.assertEqual(caught.exception.code, "inference_request_too_large")
        self.assertEqual(transport.calls, [])

    async def test_finite_cost_budget_rejects_unknown_price_before_transport(self):
        transport = FakeTransport([InferenceTransportResult(content="must not run")])
        runtime = AgentInferenceRuntime(
            portfolio=InferencePortfolio(routes=(route("groq"),)),
            budget=InferenceBudget(
                max_total_attempts=1,
                max_estimated_cost_usd=0.01,
            ),
            transport=transport,
        )
        with self.assertRaises(AgentInferenceError) as caught:
            await runtime.complete(InferenceRequest(system="system", user_payload="hello"))
        self.assertEqual(caught.exception.code, "inference_budget_exhausted")
        self.assertEqual(transport.calls, [])

    async def test_known_zero_cost_route_runs_under_finite_budget(self):
        transport = FakeTransport([InferenceTransportResult(content="local answer")])
        runtime = AgentInferenceRuntime(
            portfolio=InferencePortfolio(
                routes=(route("groq", input_cost=0.0, output_cost=0.0),)
            ),
            budget=InferenceBudget(
                max_total_attempts=1,
                max_estimated_cost_usd=0.0,
            ),
            transport=transport,
        )
        result = await runtime.complete(InferenceRequest(system="system", user_payload="hello"))
        self.assertEqual(result.content, "local answer")
        self.assertEqual(result.estimated_cost_usd, 0.0)
        self.assertEqual(len(transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
