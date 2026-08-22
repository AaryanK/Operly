import os
from unittest.mock import patch

from packages.model_runtime.openrouter_client import OpenRouterClient
from packages.studio import agent_runs, model_latency_policy, runtime_policy, source_agent
from packages.studio.model_latency_policy import studio_budget, studio_coding_model_client


def test_studio_openrouter_deadline_finishes_before_outer_model_slice():
    with patch.dict(
        os.environ,
        {
            "OPEN_ROUTER_API": "test-openrouter-key",
            "OPEN_ROUTER_TIMEOUT_SECONDS": "600",
            "OPEN_ROUTER_MAX_ATTEMPTS": "5",
            "OPERLY_MODEL_CODING_FALLBACKS": "openrouter/test-fallback",
        },
        clear=True,
    ):
        client = studio_coding_model_client("coding")

    inner = client.inner
    assert isinstance(inner, OpenRouterClient)
    assert inner.timeout_seconds == 180
    assert inner.max_attempts == 1
    assert inner.fallback_models == ["openrouter/test-fallback"]

    _, edit_total, edit_slice = studio_budget("edit")
    _, generate_total, generate_slice = studio_budget("generate")
    assert inner.timeout_seconds < edit_slice < edit_total
    assert inner.timeout_seconds < generate_slice < generate_total


def test_studio_respects_an_explicitly_tighter_provider_timeout():
    with patch.dict(
        os.environ,
        {
            "OPEN_ROUTER_API": "test-openrouter-key",
            "OPEN_ROUTER_TIMEOUT_SECONDS": "60",
            "OPEN_ROUTER_MAX_ATTEMPTS": "3",
        },
        clear=True,
    ):
        client = studio_coding_model_client("coding")

    assert client.inner.timeout_seconds == 60
    assert client.inner.max_attempts == 1


def test_latency_policy_overrides_runtime_budget_only_for_studio_modules():
    original_agent_budget = agent_runs._studio_budget
    original_agent_client = agent_runs.coding_model_client
    original_runtime_budget = runtime_policy._studio_budget
    original_source_client = source_agent.coding_model_client
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
    finally:
        agent_runs._studio_budget = original_agent_budget
        agent_runs.coding_model_client = original_agent_client
        runtime_policy._studio_budget = original_runtime_budget
        source_agent.coding_model_client = original_source_client
        model_latency_policy._APPLIED = original_applied
