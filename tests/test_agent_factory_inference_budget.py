from types import SimpleNamespace

import pytest

from packages.agents.control_plane.inference_budget import (
    FactoryInferenceBudget,
    FactoryInferenceBudgetExceeded,
    budgeted_model,
)
from packages.model_runtime import InferenceBudget, InferenceRequest, InferenceResult, ModelUsage


class FakeInferenceModel:
    async def infer(self, request):
        return InferenceResult(
            message={"role": "assistant", "content": "done"},
            model_resource_id="fake:model",
            provider="fake",
            provider_model_id="fake-model",
            latency_ms=1,
            usage=ModelUsage(input_tokens=120, output_tokens=30, total_tokens=150),
        )


@pytest.mark.asyncio
async def test_budget_reservations_prevent_parallel_double_spend():
    budget = FactoryInferenceBudget(max_tokens=1_000, max_model_calls=10)
    first = await budget.reserve(700)

    with pytest.raises(FactoryInferenceBudgetExceeded) as raised:
        await budget.reserve(400)

    assert raised.value.reason == "root_token_budget_exhausted"
    await budget.reconcile(first, 300)
    second = await budget.reserve(400)
    await budget.reconcile(second, 200)

    snapshot = budget.snapshot()
    assert snapshot["used_tokens"] == 500
    assert snapshot["model_calls"] == 2


@pytest.mark.asyncio
async def test_budgeted_model_uses_provider_usage_and_caps_output_tokens():
    budget = FactoryInferenceBudget(max_tokens=10_000, max_model_calls=10)
    model = budgeted_model(
        FakeInferenceModel(),
        root_budget=budget,
        max_output_tokens=800,
    )
    request = InferenceRequest(
        messages=({"role": "user", "content": "do the task"},),
        budget=InferenceBudget(max_output_tokens=2_000),
    )

    result = await model.infer(request)

    assert result.message["content"] == "done"
    assert model.usage == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "model_calls": 1,
    }
    assert budget.snapshot()["used_tokens"] == 150


@pytest.mark.asyncio
async def test_root_model_call_limit_blocks_additional_calls():
    budget = FactoryInferenceBudget(max_tokens=10_000, max_model_calls=1)
    model = budgeted_model(
        FakeInferenceModel(),
        root_budget=budget,
        max_output_tokens=500,
    )
    request = InferenceRequest(messages=({"role": "user", "content": "hello"},))

    await model.infer(request)
    with pytest.raises(FactoryInferenceBudgetExceeded) as raised:
        await model.infer(request)

    assert raised.value.reason == "root_model_call_budget_exhausted"
