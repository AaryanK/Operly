import os
from unittest.mock import patch

from packages.model_runtime.openrouter_client import OpenRouterClient
from packages.studio import agent_runs, model_latency_policy, runtime_policy, source_agent
from packages.studio.model_latency_policy import (
    StudioLatencyAwareCodingAgent,
    studio_budget,
    studio_coding_model_client,
)


def test_studio_openrouter_uses_bounded_default_failover_chain():
    with patch.dict(
        os.environ,
        {
            "OPEN_ROUTER_API": "test-openrouter-key",
            "OPEN_ROUTER_TIMEOUT_SECONDS": "600",
            "OPEN_ROUTER_MAX_ATTEMPTS": "5",
        },
        clear=True,
    ):
        client = studio_coding_model_client("coding")

    inner = client.inner
    assert isinstance(inner, OpenRouterClient)
    assert inner.model == "stealth/ox-alpha"
    assert inner.timeout_seconds == 60
    assert inner.max_attempts == 1
    assert inner.fallback_models == [
        "openai/gpt-oss-120b:free",
        "qwen/qwen3-coder-flash",
    ]

    _, edit_total, edit_slice = studio_budget("edit")
    _, generate_total, generate_slice = studio_budget("generate")
    worst_case_model_chain = inner.timeout_seconds * (1 + len(inner.fallback_models))
    assert worst_case_model_chain < edit_slice < edit_total
    assert worst_case_model_chain < generate_slice < generate_total


def test_studio_preserves_explicit_route_fallbacks_instead_of_defaults():
    with patch.dict(
        os.environ,
        {
            "OPEN_ROUTER_API": "test-openrouter-key",
            "OPERLY_MODEL_CODING_FALLBACKS": "openrouter/test-fallback,openrouter/test-second",
        },
        clear=True,
    ):
        client = studio_coding_model_client("coding")

    assert client.inner.fallback_models == [
        "openrouter/test-fallback",
        "openrouter/test-second",
    ]


def test_studio_specific_fallback_env_overrides_route_and_stays_bounded():
    with patch.dict(
        os.environ,
        {
            "OPEN_ROUTER_API": "test-openrouter-key",
            "OPERLY_MODEL_CODING_FALLBACKS": "openrouter/route-fallback",
            "OPERLY_STUDIO_OPENROUTER_FALLBACKS": (
                "openrouter/studio-first,openrouter/studio-second,openrouter/studio-third"
            ),
        },
        clear=True,
    ):
        client = studio_coding_model_client("coding")

    assert client.inner.fallback_models == [
        "openrouter/studio-first",
        "openrouter/studio-second",
    ]


def test_studio_respects_an_explicitly_tighter_provider_timeout():
    with patch.dict(
        os.environ,
        {
            "OPEN_ROUTER_API": "test-openrouter-key",
            "OPEN_ROUTER_TIMEOUT_SECONDS": "30",
            "OPEN_ROUTER_MAX_ATTEMPTS": "3",
        },
        clear=True,
    ):
        client = studio_coding_model_client("coding")

    assert client.inner.timeout_seconds == 30
    assert client.inner.max_attempts == 1


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
