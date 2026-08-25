from packages.software_projects.planning.live_planning import AnalystRequirement
from packages.software_projects.planning.plan_service import _interaction_acceptance, _interaction_test_ids


def _requirement(text: str, category: str = "behavior") -> AnalystRequirement:
    return AnalystRequirement(
        requirement_id="R-007",
        source_excerpt=text,
        normalized_requirement=text,
        category=category,
        priority="High",
        acceptance_criteria=["Requested behavior succeeds."],
    )


def test_interactive_requirement_gets_executable_end_to_end_acceptance():
    requirement = _requirement("Allow staff to record a transaction from a form.", "interaction")
    criteria = _interaction_acceptance(requirement)

    assert _interaction_test_ids(requirement) == ["interaction_r_007"]
    assert any("domain operation" in item for item in criteria)
    assert any("runtime error" in item for item in criteria)
    assert any("reload persistence" in item for item in criteria)


def test_noninteractive_calculation_does_not_get_fake_control_acceptance():
    requirement = _requirement("Calculate compound interest from validated values.", "calculation")

    assert _interaction_test_ids(requirement) == []
    assert _interaction_acceptance(requirement) == ["Requested behavior succeeds."]
