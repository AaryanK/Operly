import asyncio

from pydantic import BaseModel

import packages.custom_software.live_planning as live


class TinyOutput(BaseModel):
    value: str


class FakeOllamaClient:
    calls = []

    def __init__(self, *, model, fallback_models):
        self.model = model
        self.last_model = model
        self.max_attempts = 3

    async def chat(self, messages):
        self.calls.append(self.model)
        return {"role": "assistant", "content": '{"value":"ok"}'}


def test_planning_timeout_moves_to_next_role_model(monkeypatch):
    FakeOllamaClient.calls = []
    monkeypatch.setattr(live, "OllamaClient", FakeOllamaClient)
    real_wait_for = asyncio.wait_for
    waits = 0

    async def timeout_first(awaitable, timeout):
        nonlocal waits
        waits += 1
        if waits == 1:
            awaitable.close()
            raise asyncio.TimeoutError()
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(live.asyncio, "wait_for", timeout_first)
    monkeypatch.setenv("OPERLY_MODEL_REQUIREMENTS_ANALYST", "gemma4:31b")
    monkeypatch.setenv("OPERLY_MODEL_REQUIREMENTS_ANALYST_FALLBACKS", "nemotron-3-nano:30b,gpt-oss:20b")

    result = asyncio.run(live.OllamaPlanningClient().generate_structured(
        role="requirements_analyst",
        context=live.PlanningContextPacket(role="requirements_analyst", untrusted_requirements={"prompt": "test"}),
        output_schema=TinyOutput,
        request_id="request-1",
        timeout_seconds=30,
    ))

    assert result.failure_classification is None
    assert result.structured_output == {"value": "ok"}
    assert result.model_id == "nemotron-3-nano:30b"
    assert FakeOllamaClient.calls == ["nemotron-3-nano:30b"]


def test_runtime_default_gives_single_planning_model_full_documented_slice(monkeypatch):
    FakeOllamaClient.calls = []
    monkeypatch.setattr(live, "OllamaClient", FakeOllamaClient)
    monkeypatch.delenv("OPERLY_PLANNING_MODEL_SLICE_SECONDS", raising=False)
    monkeypatch.setenv("OPERLY_MODEL_REQUIREMENTS_ANALYST", "gemma4:31b")
    monkeypatch.setenv("OPERLY_MODEL_REQUIREMENTS_ANALYST_FALLBACKS", "")
    observed = []
    real_wait_for = asyncio.wait_for

    async def capture_timeout(awaitable, timeout):
        observed.append(timeout)
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(live.asyncio, "wait_for", capture_timeout)
    result = asyncio.run(live.OllamaPlanningClient().generate_structured(
        role="requirements_analyst",
        context=live.PlanningContextPacket(role="requirements_analyst", untrusted_requirements={"prompt": "test"}),
        output_schema=TinyOutput,
        request_id="request-default-slice",
        timeout_seconds=120,
    ))

    assert result.structured_output == {"value": "ok"}
    assert observed == [120]


class AlwaysTimeoutPlanningClient:
    async def generate_structured(self, *, role, context, output_schema, request_id, timeout_seconds, attempt=1):
        return live.StructuredModelResult(
            provider="ollama",
            model_id="gemma4:31b",
            request_id=request_id,
            attempt=attempt,
            latency_ms=120_000,
            failure_classification=live.FailureClass.TIMEOUT,
            validation_errors=["internal timeout payload"],
            context_digest=context.digest(),
        )


def test_exhausted_timeout_becomes_safe_retriable_planner_error():
    orchestrator = live.LivePlanningOrchestrator(AlwaysTimeoutPlanningClient(), max_attempts=2)

    try:
        asyncio.run(orchestrator._call(
            "requirements_analyst",
            live.PlanningContextPacket(role="requirements_analyst", untrusted_requirements={"prompt": "test"}),
            TinyOutput,
        ))
    except live.PlannerUnavailable as error:
        message = str(error)
    else:
        raise AssertionError("timeout should be retriable and unavailable")

    assert "Please try again" in message
    assert "requirements_analyst" not in message
    assert "internal timeout payload" not in message
