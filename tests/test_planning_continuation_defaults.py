from packages.custom_software.plan_service import _clarified_prompt, _planning_concurrency


def test_delegated_clarification_forces_conventional_defaults_without_reasking():
    prompt = _clarified_prompt(
        "Build a booking site with four services and pet sizes.",
        [
            {
                "questions": [
                    "What are the four services?",
                    "Which pet-size categories should be used?",
                ],
                "answer": "Use OPERLY's best judgment from my request and platform defaults.",
            }
        ],
    )

    assert "choose sensible conventional defaults" in prompt
    assert "Do not ask another question" in prompt
    assert "Return a complete implementation-ready plan now" in prompt


def test_invalid_planning_concurrency_falls_back_to_one(monkeypatch):
    monkeypatch.setenv("OPERLY_MAX_CONCURRENT_PLANS", "invalid")
    assert _planning_concurrency() == 1


def test_planning_concurrency_is_safely_bounded(monkeypatch):
    monkeypatch.setenv("OPERLY_MAX_CONCURRENT_PLANS", "100")
    assert _planning_concurrency() == 8
