from types import SimpleNamespace

import pytest

from packages.capabilities.model_provider import ModelInvocationProvider
from packages.model_runtime.service import ModelInvocationResult
from packages.tasks.workflow import _workflow_model_target, validate_workflow


class _FakeModelService:
    def __init__(self):
        self.calls = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return ModelInvocationResult(
            provider="test-provider",
            model="test-model",
            resource_id="test-provider:test-model",
            capability=str(kwargs["capability"]),
            selected_tags=("reliable",),
            content="specialist-result",
            latency_ms=12,
            usage={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
        )


def _context(*, depth=0):
    return SimpleNamespace(
        invocation={
            "metadata": {
                "runtime_run_id": "root-run-1",
                "ai_delegation_depth": depth,
            }
        },
        execution_id="action-1",
        db=None,
        tenant_id="tenant-1",
        actor_id="user-1",
    )


def test_semantic_ai_capabilities_share_existing_model_permission_and_hide_route_selection():
    definitions = {item.id: item for item in ModelInvocationProvider.capabilities}
    expected = {
        "ai.generate",
        "ai.reason",
        "ai.plan",
        "ai.code.generate",
        "ai.code.repair",
        "ai.code.review",
        "ai.extract.requirements",
    }
    assert expected.issubset(definitions)

    for capability_id in expected:
        definition = definitions[capability_id]
        assert definition.permissions == ("model:invoke",)
        assert definition.category == "ai"
        properties = definition.input_schema["properties"]
        assert "provider" not in properties
        assert "model" not in properties
        assert "objective" in properties


@pytest.mark.asyncio
async def test_code_repair_reuses_model_runtime_and_parent_keeps_objective():
    service = _FakeModelService()
    provider = ModelInvocationProvider(model_service=service)

    result = await provider.execute(
        _context(),
        "ai.code.repair",
        {
            "objective": "Repair the failing attendance endpoint",
            "context": "runner says assertion failed",
        },
    )

    assert result.success is True
    assert len(service.calls) == 1
    call = service.calls[0]
    assert call["capability"] == "coding"
    assert "coding" in call["prefer_tags"]
    assert "reasoning" in call["prefer_tags"]
    assert result.evidence["ai_capability"] == "ai.code.repair"
    assert result.evidence["content"] == "specialist-result"
    delegation = result.evidence["delegation"]
    assert delegation["parent_run_id"] == "root-run-1"
    assert delegation["parent_execution_id"] == "action-1"
    assert delegation["depth"] == 1
    assert delegation["child_tools_exposed"] is False
    assert delegation["terminal_for_parent"] is False
    assert delegation["parent_retains_objective"] is True


@pytest.mark.asyncio
async def test_ai_delegation_is_bounded_to_one_specialist_level():
    service = _FakeModelService()
    provider = ModelInvocationProvider(model_service=service)

    result = await provider.execute(
        _context(depth=1),
        "ai.reason",
        {"objective": "Delegate again"},
    )

    assert result.success is False
    assert result.evidence["reason"] == "ai_delegation_depth_exceeded"
    assert service.calls == []


def test_existing_workflow_model_nodes_map_to_semantic_ai_capabilities():
    assert _workflow_model_target({}) == ("ai.reason", None)
    assert _workflow_model_target({"capability": "reasoning"}) == ("ai.reason", None)
    assert _workflow_model_target({"capability": "coding"}) == ("ai.code.generate", None)
    assert _workflow_model_target({"capability": "text"}) == ("ai.generate", None)
    assert _workflow_model_target({"ai_capability": "ai.code.repair"}) == (
        "ai.code.repair",
        None,
    )
    assert _workflow_model_target({"capability": "vision"}) == ("model.invoke", "vision")


def test_workflow_accepts_explicit_ai_capability_and_rejects_non_ai_override():
    workflow = {
        "steps": [
            {
                "type": "model",
                "id": "repair",
                "ai_capability": "ai.code.repair",
                "objective": "Repair this build",
            }
        ]
    }
    assert validate_workflow(workflow)["steps"][0]["ai_capability"] == "ai.code.repair"

    invalid = {
        "steps": [
            {
                "type": "model",
                "id": "repair",
                "ai_capability": "groq.qwen",
                "objective": "Repair this build",
            }
        ]
    }
    with pytest.raises(ValueError, match="workflow_ai_capability_invalid"):
        validate_workflow(invalid)
