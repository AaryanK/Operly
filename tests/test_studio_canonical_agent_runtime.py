from types import SimpleNamespace

import pytest

import packages.coding_harness.runtime_agent as runtime_module
import packages.coding_harness.studio_controller as removed_studio_runtime
from packages.coding_harness import AgentRuntimeCodingAgent
from packages.coding_harness.opencode_agent import (
    CapabilityCodingAgent as DirectCapabilityCodingAgent,
    OpenCodeStyleCodingAgent as DirectOpenCodeStyleCodingAgent,
)
from packages.coding_harness.source_service import OpenCodeStyleCodingAgent as SourceServiceCodingAgent
from packages.capabilities.software_build_provider import CapabilityCodingAgent as SoftwareBuildCodingAgent


def test_all_software_agent_exports_resolve_to_canonical_runtime_adapter():
    assert DirectCapabilityCodingAgent is AgentRuntimeCodingAgent
    assert DirectOpenCodeStyleCodingAgent is AgentRuntimeCodingAgent
    assert SourceServiceCodingAgent is AgentRuntimeCodingAgent
    assert SoftwareBuildCodingAgent is AgentRuntimeCodingAgent


def test_removed_studio_runtime_is_only_a_fail_closed_compatibility_tombstone():
    first = SimpleNamespace(source_version=7, bundle_digest="sha256:" + "1" * 64, id="s1")
    same = SimpleNamespace(source_version=7, bundle_digest="sha256:" + "1" * 64, id="s1")
    assert removed_studio_runtime.source_scoped_idempotency_key("build", first) == (
        removed_studio_runtime.source_scoped_idempotency_key("build", same)
    )

    with pytest.raises(RuntimeError, match="Studio-specific agent orchestration was removed"):
        import asyncio

        asyncio.run(removed_studio_runtime.run_studio_generation())


@pytest.mark.asyncio
async def test_software_planning_session_runs_through_canonical_agent_runtime(monkeypatch):
    seen = {}

    class FakeCanonicalRuntime:
        def __init__(self, **kwargs):
            seen["init"] = dict(kwargs)

        async def run(self, **kwargs):
            seen["run"] = kwargs
            schemas = await kwargs["schemas"]()
            names = {item["function"]["name"] for item in schemas}
            assert "finish_plan" in names
            observation = await kwargs["invoke"](
                "finish_plan",
                {"plan": "Inspect the project and implement the requested change."},
                "call-1",
            )
            assert observation["status"] == "VERIFIED"
            messages = list(kwargs["messages"])
            messages.append({"role": "assistant", "content": "Plan completed."})
            return {
                "message": "Plan completed.",
                "messages": messages,
                "stop_reason": "completed",
                "stopped": False,
                "trace": [],
                "budget": {},
            }

    monkeypatch.setattr(runtime_module, "AgentRuntime", FakeCanonicalRuntime)
    agent = AgentRuntimeCodingAgent(client=object(), max_steps=12)
    plan = await agent.plan('{"objective":"Make a useful application"}')

    assert plan == "Inspect the project and implement the requested change."
    assert seen["run"]["inference_metadata"]["runtime_component"] == "software_agent_runtime"
    assert seen["run"]["inference_metadata"]["worker_role"] == "coding_agent"
