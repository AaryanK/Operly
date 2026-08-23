from packages.model_runtime.contracts import InferenceRequest
from packages.model_runtime.registry import ModelRegistry
from packages.model_runtime.requirements import ModelRequirements
from packages.model_runtime.task_routing import TaskRouteDecision, requirements_for_task


def test_registry_selector_can_satisfy_combined_capabilities_without_role():
    registry = ModelRegistry()
    registry.configure(
        id="reasoner",
        provider="test",
        model="reasoner",
        capabilities={"text", "reasoning"},
        tags={"reliable"},
    )
    registry.configure(
        id="builder",
        provider="test",
        model="builder",
        capabilities={"text", "reasoning", "coding", "tools"},
        tags={"coding", "reliable"},
    )
    requirements = ModelRequirements(
        requires=frozenset({"text", "coding", "tools"}),
        prefer_tags=frozenset({"coding", "reliable"}),
    )
    selected = registry.resolve(requirements.selector())
    assert selected.id == "builder"


def test_task_requirements_union_semantics_with_exposed_tools():
    decision = TaskRouteDecision(
        task_type="planning",
        role="planner",
        tool_policy="read_then_propose",
        confidence=0.9,
        reason="test",
    )
    request = InferenceRequest(
        messages=({"role": "user", "content": "Plan this and inspect my available data."},),
        tools=({"type": "function", "function": {"name": "inspect", "parameters": {}}},),
    )
    requirements = requirements_for_task(decision, request)
    assert {"text", "reasoning", "tools"}.issubset(requirements.requires)


def test_context_requirement_is_not_a_semantic_role():
    decision = TaskRouteDecision(
        task_type="coding_or_studio",
        role="coding",
        tool_policy="workspace_write_with_validation",
        confidence=0.9,
        reason="test",
    )
    request = InferenceRequest(
        messages=({"role": "user", "content": "x" * 80000},),
        tools=(),
    )
    requirements = requirements_for_task(decision, request)
    assert "coding" in requirements.requires
    assert requirements.min_context_tokens == 64_000
