import os
from unittest.mock import patch

from packages.model_runtime.registry import ModelChatAdapter, ModelPool
from packages.studio import agent_runs, model_latency_policy, runtime_policy, source_agent
from packages.studio.model_latency_policy import (
    StudioLatencyAwareCodingAgent,
    studio_budget,
    studio_coding_model_client,
)
from packages.studio.model_trace import TracingModelChatClient, redact_trace_value


def _adapter(client) -> ModelChatAdapter:
    tracing = client.inner
    assert isinstance(tracing, TracingModelChatClient)
    adapter = tracing.inner
    assert isinstance(adapter, ModelChatAdapter)
    return adapter


def _provider_model_ids(adapter: ModelChatAdapter) -> list[str]:
    model = adapter.model
    if isinstance(model, ModelPool):
        return [item.provider_model_id for item in model.models]
    return [model.provider_model_id]


def test_studio_uses_bounded_provider_neutral_model_pool():
    with patch.dict(os.environ, {}, clear=True):
        client = studio_coding_model_client("coding")
        adapter = _adapter(client)

    # Context bounding is outermost and tracing sits immediately in front of the
    # generic ModelChatAdapter. Therefore the trace sees the compacted packet that
    # is actually converted into an InferenceRequest.
    assert isinstance(client.inner, TracingModelChatClient)
    assert isinstance(adapter.model, ModelPool)
    assert _provider_model_ids(adapter) == [
        "stealth/ox-alpha",
        "openai/gpt-oss-120b:free",
        "qwen/qwen3-coder-flash",
    ]
    assert adapter.budget.timeout_seconds == 60
    assert adapter.budget.attempts_per_model == 1
    assert adapter.budget.max_models == 3
    assert adapter.budget.max_output_tokens == 16_384

    _, edit_total, edit_slice = studio_budget("edit")
    _, generate_total, generate_slice = studio_budget("generate")
    worst_case_model_chain = adapter.budget.timeout_seconds * adapter.budget.max_models
    assert worst_case_model_chain < edit_slice < edit_total
    assert worst_case_model_chain < generate_slice < generate_total


def test_studio_preserves_explicit_role_fallbacks_through_model_runtime():
    with patch.dict(
        os.environ,
        {
            "OPERLY_MODEL_CODING_FALLBACKS": "openrouter/test-fallback,openrouter/test-second",
        },
        clear=True,
    ):
        adapter = _adapter(studio_coding_model_client("coding"))

    assert _provider_model_ids(adapter) == [
        "stealth/ox-alpha",
        "openrouter/test-fallback",
        "openrouter/test-second",
    ]


def test_studio_provider_specific_legacy_fallback_env_is_not_authority():
    with patch.dict(
        os.environ,
        {
            "OPERLY_MODEL_CODING_FALLBACKS": "openrouter/route-fallback",
            "OPERLY_STUDIO_OPENROUTER_FALLBACKS": (
                "openrouter/studio-first,openrouter/studio-second,openrouter/studio-third"
            ),
        },
        clear=True,
    ):
        adapter = _adapter(studio_coding_model_client("coding"))

    # Studio no longer has an OpenRouter-specific fallback policy. The shared model
    # runtime owns candidate configuration, so only the generic role fallback applies.
    assert _provider_model_ids(adapter) == [
        "stealth/ox-alpha",
        "openrouter/route-fallback",
    ]


def test_studio_timeout_and_output_budget_are_provider_neutral():
    with patch.dict(
        os.environ,
        {
            "OPEN_ROUTER_TIMEOUT_SECONDS": "30",
            "OPEN_ROUTER_MAX_ATTEMPTS": "3",
            "OPEN_ROUTER_MAX_TOKENS": "4096",
        },
        clear=True,
    ):
        adapter = _adapter(studio_coding_model_client("coding"))

    # Provider adapter settings may be tighter, but Studio's orchestration contract
    # remains expressed only through InferenceBudget and never inspects that adapter.
    assert adapter.budget.timeout_seconds == 60
    assert adapter.budget.attempts_per_model == 1
    assert adapter.budget.max_models == 3
    assert adapter.budget.max_output_tokens == 16_384


def test_model_trace_redacts_secret_shaped_values_without_hiding_normal_prompt_data():
    source = {
        "messages": [
            {"role": "user", "content": "keep this exact source text"},
            {"role": "tool", "content": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"},
        ],
        "metadata": {
            "api_key": "sk-super-secret-value-123456",
            "ordinary": "visible",
        },
    }
    redacted = redact_trace_value(source)

    assert redacted["messages"][0]["content"] == "keep this exact source text"
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted["messages"][1]["content"]
    assert redacted["metadata"]["api_key"] == "[REDACTED]"
    assert redacted["metadata"]["ordinary"] == "visible"


def test_latency_aware_agent_can_reach_declared_studio_budget():
    class StubClient:
        async def chat(self, messages, tools=None):
            return {"role": "assistant", "content": "ok"}

    agent = StudioLatencyAwareCodingAgent(client=StubClient())
    assert agent.max_seconds >= 600
    assert agent.model_slice_seconds >= 195

    _, edit_total, edit_slice = studio_budget("edit")
    agent.max_seconds = min(agent.max_seconds, edit_total)
    agent.model_slice_seconds = min(
        agent.model_slice_seconds,
        edit_slice,
        agent.max_seconds,
    )
    assert agent.max_seconds == 420
    assert agent.model_slice_seconds == 195


def test_latency_policy_overrides_runtime_budget_only_for_studio_modules():
    original_agent_budget = agent_runs._studio_budget
    original_agent_client = agent_runs.coding_model_client
    original_agent_class = agent_runs.OpenCodeStyleCodingAgent
    original_runtime_budget = runtime_policy._studio_budget
    original_source_client = source_agent.coding_model_client
    original_source_class = source_agent.OpenCodeStyleCodingAgent
    original_applied = model_latency_policy._APPLIED

    try:
        model_latency_policy._APPLIED = False
        model_latency_policy.apply_studio_model_latency_policy()
        model_latency_policy.apply_studio_model_latency_policy()

        assert agent_runs._studio_budget("edit") == (10, 420, 195)
        assert agent_runs._studio_budget("generate") == (20, 600, 195)
        assert runtime_policy._studio_budget("edit") == (10, 420, 195)
        assert agent_runs.coding_model_client is studio_coding_model_client
        assert source_agent.coding_model_client is studio_coding_model_client
        assert agent_runs.OpenCodeStyleCodingAgent is StudioLatencyAwareCodingAgent
        assert source_agent.OpenCodeStyleCodingAgent is StudioLatencyAwareCodingAgent
    finally:
        agent_runs._studio_budget = original_agent_budget
        agent_runs.coding_model_client = original_agent_client
        agent_runs.OpenCodeStyleCodingAgent = original_agent_class
        runtime_policy._studio_budget = original_runtime_budget
        source_agent.coding_model_client = original_source_client
        source_agent.OpenCodeStyleCodingAgent = original_source_class
        model_latency_policy._APPLIED = original_applied
