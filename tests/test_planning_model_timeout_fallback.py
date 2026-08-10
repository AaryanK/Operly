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
    monkeypatch.setenv("OPERLY_MODEL_REQUIREMENTS_ANALYST", "nemotron-3-ultra")
    monkeypatch.setenv("OPERLY_MODEL_REQUIREMENTS_ANALYST_FALLBACKS", "nemotron-3-super,gemma4:31b")

    result = asyncio.run(live.OllamaPlanningClient().generate_structured(
        role="requirements_analyst",
        context=live.PlanningContextPacket(role="requirements_analyst", untrusted_requirements={"prompt": "test"}),
        output_schema=TinyOutput,
        request_id="request-1",
        timeout_seconds=30,
    ))

    assert result.failure_classification is None
    assert result.structured_output == {"value": "ok"}
    assert result.model_id == "nemotron-3-super"
    assert FakeOllamaClient.calls == ["nemotron-3-super"]
