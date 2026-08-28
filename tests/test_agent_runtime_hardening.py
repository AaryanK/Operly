from unittest.mock import patch

import pytest
from fastapi import HTTPException

from apps.api.agent_router import _run_agent
from packages.agents.runtime import AgentRuntime
from packages.business_brain.types import AgentInput
from packages.database.company_models import BusinessActionRecord, BusinessEventRecord
from packages.model_runtime.contracts import (
    InferenceRequest,
    InferenceResult,
    ModelInferenceError,
    ModelTraits,
)
from packages.model_runtime.openai_compatible_client import _provider_generated_tool_error
from packages.model_runtime.registry import ModelPool


class CapturingModel:
    id = "capture"
    tags = frozenset({"test"})
    capabilities = frozenset({"text", "tools"})
    traits = ModelTraits()

    def __init__(self):
        self.request = None

    async def infer(self, request):
        self.request = request
        return InferenceResult(
            message={"role": "assistant", "content": "hello"},
            model_resource_id=self.id,
            provider="test",
            provider_model_id=self.id,
            latency_ms=1,
        )


def _search_tool():
    return {
        "type": "function",
        "function": {
            "name": "capability.search",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "maximum": 20},
                },
            },
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["hy there", "hello", "hey!", "thanks", "good morning"])
async def test_trivial_conversation_never_forwards_tool_schemas(text):
    target = CapturingModel()
    runtime = AgentRuntime(max_steps=2)

    async def schemas():
        return [_search_tool()]

    async def invoke(name, arguments, call_id):
        raise AssertionError(f"trivial conversation invoked {name}: {arguments} {call_id}")

    await runtime.run(
        model=target,
        messages=[{"role": "user", "content": text}],
        schemas=schemas,
        invoke=invoke,
    )

    assert target.request is not None
    assert target.request.tools == ()


@pytest.mark.asyncio
async def test_mixed_greeting_with_real_action_keeps_tool_access():
    target = CapturingModel()
    runtime = AgentRuntime(max_steps=2)

    async def schemas():
        return [_search_tool()]

    async def invoke(name, arguments, call_id):
        return {"ok": True}

    await runtime.run(
        model=target,
        messages=[{"role": "user", "content": "hello, email John saying hi"}],
        schemas=schemas,
        invoke=invoke,
    )

    assert target.request is not None
    assert target.request.tools == (_search_tool(),)


def test_causation_columns_accept_provider_trace_identifiers():
    assert BusinessActionRecord.__table__.c.causation_id.type.length == 160
    assert BusinessEventRecord.__table__.c.causation_id.type.length == 160


def test_groq_style_generated_tool_validation_is_model_failure_not_client_error():
    detail = (
        "Tool call validation failed: parameters for tool capability.search "
        "did not match schema: /limit must be <= 20 but found 100"
    )
    assert _provider_generated_tool_error(400, detail, [_search_tool()]) is True
    assert _provider_generated_tool_error(400, detail, []) is False
    assert _provider_generated_tool_error(401, detail, [_search_tool()]) is False


class RejectingToolModel:
    id = "rejecting"
    provider = "groq"
    tags = frozenset({"tools"})
    capabilities = frozenset({"text", "tools"})
    traits = ModelTraits()

    async def infer(self, request):
        raise ModelInferenceError(
            "generated invalid tool call",
            classification="tool_call_validation",
            retryable=True,
            provider="groq",
            model_id=self.id,
        )


class WorkingToolModel:
    id = "working"
    provider = "test-ok"
    tags = frozenset({"tools"})
    capabilities = frozenset({"text", "tools"})
    traits = ModelTraits()

    async def infer(self, request):
        return InferenceResult(
            message={"role": "assistant", "content": "recovered"},
            model_resource_id=self.id,
            provider=self.provider,
            provider_model_id=self.id,
            latency_ms=1,
        )


@pytest.mark.asyncio
async def test_model_pool_fails_over_after_generated_tool_validation_failure():
    # This test is about generated-tool validation failover. Synthetic route
    # eligibility is intentionally outside its unit boundary.
    with patch("packages.model_runtime.scoring.route_is_zero_cost", return_value=True):
        pool = ModelPool((RejectingToolModel(), WorkingToolModel()))
        result = await pool.infer(
            InferenceRequest(
                messages=({"role": "user", "content": "find something"},),
                tools=(_search_tool(),),
            )
        )
    assert result.provider_model_id == "working"
    assert result.message["content"] == "recovered"


@pytest.mark.asyncio
async def test_agent_http_boundary_normalizes_model_inference_error():
    class FailingService:
        async def run(self, request):
            raise ModelInferenceError(
                "provider generated invalid tools",
                classification="tool_call_validation",
                retryable=True,
                provider="groq",
                model_id="example",
            )

    request = AgentInput(
        tenant_id="tenant",
        principal_id="principal",
        actor_name="Owner",
        channel="web",
        text="hello",
    )
    with patch("apps.api.agent_router.get_agent_service", return_value=FailingService()):
        with pytest.raises(HTTPException) as captured:
            await _run_agent(None, request)

    assert captured.value.status_code == 503
    assert captured.value.detail == {
        "error": "model_inference_failed",
        "classification": "tool_call_validation",
        "retryable": True,
    }
