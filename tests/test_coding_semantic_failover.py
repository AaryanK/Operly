import pytest

from packages.coding_harness.model_client import SemanticFailoverCodingClient
from packages.coding_harness.opencode_agent import OpenCodeStyleCodingAgent
from packages.model_runtime.contracts import InferenceResult, ModelTraits
from packages.model_runtime.registry import ModelChatAdapter, ModelPool


@pytest.fixture(autouse=True)
def _disable_zero_cost_filter_for_fake_models(monkeypatch):
    """These tests exercise semantic failover, not provider billing eligibility."""
    monkeypatch.setenv("OPERLY_FREE_MODELS_ONLY", "0")


class _NoToolModel:
    def __init__(self, model_id: str, *, provider: str = "provider-a") -> None:
        self.id = model_id
        self.provider = provider
        self.provider_model_id = model_id
        self.tags = frozenset({"coding", "tools"})
        self.capabilities = frozenset({"text", "coding", "tools"})
        self.traits = ModelTraits()
        self.calls = 0

    async def infer(self, request):
        self.calls += 1
        return InferenceResult(
            message={
                "role": "assistant",
                "content": "I can implement this, but I did not use the supplied project tools.",
            },
            model_resource_id=self.id,
            provider=self.provider,
            provider_model_id=self.provider_model_id,
            latency_ms=1,
            finish_reason="stop",
        )


class _ToolModel:
    def __init__(self, model_id: str, *, provider: str = "provider-a") -> None:
        self.id = model_id
        self.provider = provider
        self.provider_model_id = model_id
        self.tags = frozenset({"coding", "tools"})
        self.capabilities = frozenset({"text", "coding", "tools"})
        self.traits = ModelTraits()
        self.calls = 0

    async def infer(self, request):
        self.calls += 1
        tool_names = {
            item.get("function", {}).get("name")
            for item in request.tools
            if isinstance(item, dict)
        }
        if "finish_plan" in tool_names:
            calls = [
                {
                    "type": "function",
                    "function": {
                        "name": "finish_plan",
                        "arguments": {"plan": "Use the approved specification and project tools."},
                    },
                }
            ]
        else:
            calls = [
                {
                    "type": "function",
                    "function": {"name": "list", "arguments": {"prefix": ""}},
                }
            ]
        return InferenceResult(
            message={"role": "assistant", "content": "", "tool_calls": calls},
            model_resource_id=self.id,
            provider=self.provider,
            provider_model_id=self.provider_model_id,
            latency_ms=1,
            finish_reason="tool_calls",
        )


def _client(*models):
    return SemanticFailoverCodingClient(ModelChatAdapter(ModelPool(models, id="coding-test")))


@pytest.mark.asyncio
async def test_transport_success_without_required_tools_fails_over_to_next_model():
    first = _NoToolModel("prose-only")
    second = _ToolModel("tool-capable")
    client = _client(first, second)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "list",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    response = await client.chat(
        [
            {"role": "system", "content": "Use project tools."},
            {"role": "user", "content": "Build the approved application."},
        ],
        tools,
    )

    assert response["tool_calls"][0]["function"]["name"] == "list"
    assert first.calls == 1
    assert second.calls == 1


@pytest.mark.asyncio
async def test_semantic_tool_failure_is_model_local_not_provider_wide():
    first = _NoToolModel("bad-tools", provider="same-provider")
    second = _ToolModel("good-tools", provider="same-provider")
    client = _client(first, second)

    response = await client.chat(
        [{"role": "user", "content": "Use the tool."}],
        [
            {
                "type": "function",
                "function": {
                    "name": "list",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )

    assert response.get("tool_calls")
    assert first.calls == 1
    assert second.calls == 1


@pytest.mark.asyncio
async def test_real_coding_plan_session_recovers_from_prose_only_candidate():
    first = _NoToolModel("prose-only")
    second = _ToolModel("tool-capable", provider="provider-b")
    agent = OpenCodeStyleCodingAgent(client=_client(first, second), max_steps=4)

    plan = await agent.plan("Approved generated-Solution specification")

    assert plan == "Use the approved specification and project tools."
    assert first.calls == 1
    assert second.calls == 1
