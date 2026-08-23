import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from apps.api.main import app
from packages.model_runtime import client_context
from packages.model_runtime.contracts import ModelUsage
from packages.model_runtime.registry import ModelAttemptEvent
from packages.solutions.model_trace import (
    TracingModelChatClient,
    begin,
    end,
    snapshot,
    telemetry_sink,
    trace_client,
)


@dataclass
class FakeBudget:
    timeout_seconds: float = 12.0


class FakeModelClient:
    def __init__(self):
        self.model = SimpleNamespace(
            id="role:planner",
            provider="test-provider",
            provider_model_id="test-model",
        )
        self.budget = FakeBudget()
        self.last_result = None

    @property
    def last_model(self):
        return "test-model"

    async def chat(self, messages, tools=None):
        self.last_result = SimpleNamespace(
            model_resource_id="role:planner",
            provider="test-provider",
            provider_model_id="test-model",
            latency_ms=17,
            finish_reason="stop",
            attempt=1,
            usage=ModelUsage(input_tokens=10, output_tokens=4, total_tokens=14),
        )
        return {"content": "ok", "usage": {"total_tokens": 14}}


class SolutionGenerationObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_trace_captures_redacted_packet_response_usage_and_attempts(self):
        token = begin("job-1")
        try:
            client = trace_client(FakeModelClient())
            await telemetry_sink(
                ModelAttemptEvent(
                    phase="start",
                    resource_id="role:planner",
                    provider="test-provider",
                    provider_model_id="test-model",
                    attempt=1,
                )
            )
            await client.chat(
                [{"role": "user", "content": "Bearer abcdefghijklmnopqrstuvwxyz"}],
                [],
            )
            await telemetry_sink(
                ModelAttemptEvent(
                    phase="success",
                    resource_id="role:planner",
                    provider="test-provider",
                    provider_model_id="test-model",
                    attempt=1,
                    latency_ms=17,
                )
            )
            trace = snapshot()
        finally:
            end(token)

        self.assertTrue(trace["aiInvoked"])
        self.assertEqual(len(trace["modelAttempts"]), 2)
        self.assertEqual(trace["modelAttempts"][-1]["latencyMs"], 17)
        self.assertEqual(trace["modelCalls"][0]["phase"], "request")
        self.assertIn("Bearer [REDACTED]", str(trace["modelCalls"][0]))
        self.assertEqual(trace["modelCalls"][-1]["usage"]["total_tokens"], 14)
        self.assertEqual(trace["modelCalls"][-1]["providerModelId"], "test-model")

    async def test_model_client_decoration_is_context_local_not_a_global_monkeypatch(self):
        original = client_context._base_client_for_role
        client_context._base_client_for_role = lambda role, **kwargs: FakeModelClient()
        try:
            outside = client_context.model_chat_client_for_role("planner")
            self.assertNotIsInstance(outside, TracingModelChatClient)

            token = begin("job-2")
            try:
                inside = client_context.model_chat_client_for_role("planner")
                self.assertIsInstance(inside, TracingModelChatClient)
            finally:
                end(token)

            outside_again = client_context.model_chat_client_for_role("planner")
            self.assertNotIsInstance(outside_again, TracingModelChatClient)
        finally:
            client_context._base_client_for_role = original

    def test_exactly_one_canonical_compose_route_is_registered(self):
        routes = [
            route
            for route in app.routes
            if getattr(route, "path", "") == "/api/solutions/compose"
            and "POST" in (getattr(route, "methods", set()) or set())
        ]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].endpoint.__module__, "apps.api.solutions_router")

    def test_browser_exposes_failed_generation_trace_and_retry(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "apps"
            / "web"
            / "static"
            / "unified-solution-studio.js"
        ).read_text(encoding="utf-8")
        self.assertIn("initial_generation_failed", source)
        self.assertIn("/generation-trace", source)
        self.assertIn("Retry generation", source)
        self.assertIn("renderManagedGenerationTrace", source)
        self.assertIn("retryManagedGeneration", source)


if __name__ == "__main__":
    unittest.main()
