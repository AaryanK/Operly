import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import APIRouter

from apps.api.solution_generation_router import retire_legacy_compose_route
from packages.model_runtime.contracts import ModelUsage
from packages.model_runtime.registry import ModelAttemptEvent
from packages.solutions.model_trace import begin, end, snapshot, telemetry_sink, trace_client


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

    def test_retire_legacy_compose_keeps_one_generation_authority(self):
        canonical = APIRouter(prefix="/api/solutions")

        @canonical.post("/compose")
        async def old_compose():
            return {}

        @canonical.get("/{solution_id}")
        async def get_solution(solution_id: str):
            return {"id": solution_id}

        retire_legacy_compose_route(canonical)
        post_compose = [
            route
            for route in canonical.routes
            if route.path == "/api/solutions/compose" and "POST" in (route.methods or set())
        ]
        get_routes = [route for route in canonical.routes if route.path == "/api/solutions/{solution_id}"]
        self.assertEqual(post_compose, [])
        self.assertEqual(len(get_routes), 1)


if __name__ == "__main__":
    unittest.main()
