from packages.model_runtime.contracts import InferenceBudget
from packages.model_runtime.task_routing import TaskRouteDecision, _task_budget


def _decision(task_type: str) -> TaskRouteDecision:
    return TaskRouteDecision(
        task_type=task_type,
        role="coding" if task_type == "coding_or_studio" else "business_agent",
        tool_policy="test",
        confidence=1.0,
        reason="test",
    )


def test_tool_driven_coding_turn_does_not_reserve_long_form_completion_budget():
    budget = _task_budget(_decision("coding_or_studio"), None, has_tools=True)

    assert budget.max_output_tokens == 2_048
    assert budget.max_output_tokens < 8_000


def test_non_tool_coding_turn_no_longer_defaults_to_twelve_thousand_tokens():
    budget = _task_budget(_decision("coding_or_studio"), None, has_tools=False)

    assert budget.max_output_tokens == 6_000


def test_explicit_output_budget_remains_authoritative():
    budget = _task_budget(
        _decision("coding_or_studio"),
        InferenceBudget(max_output_tokens=3_500),
        has_tools=True,
    )

    assert budget.max_output_tokens == 3_500
