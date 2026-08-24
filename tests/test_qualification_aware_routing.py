import json
import os
from unittest.mock import patch

from packages.model_runtime.catalog import model_resources
from packages.model_runtime.qualification import (
    apply_model_qualification_overrides,
    qualification_for,
    qualification_preference_tags,
    qualification_profiles,
)
from packages.model_runtime.registry import ModelRegistry
from packages.model_runtime.routing_policy import role_routing_profile


def _groq_env():
    return {
        "groq_api_key": "test-groq",
        "OPERLY_MODEL_AUTO_PORTFOLIO": "1",
    }


def test_qwen_deep_evidence_promotes_tools_and_task_tags_without_rewriting_catalog():
    with patch.dict(os.environ, _groq_env(), clear=True):
        apply_model_qualification_overrides()
        cards = {f"{item.provider}:{item.id}": item for item in model_resources()}

    qwen = cards["groq:qwen/qwen3.6-27b"]
    assert "tools" in qwen.capabilities
    assert "qualified-tools" in qwen.tags
    assert "qualified-coding" in qwen.tags
    assert "qualified-repair" in qwen.tags
    assert "qualified-planning" not in qwen.tags

    evidence = qualification_for("groq:qwen/qwen3.6-27b")
    assert evidence is not None
    assert evidence.status("planning") == "inconclusive"
    assert evidence.task_score("ai.code.repair") > evidence.task_score("ai.plan")


def test_existing_repair_role_prefers_deep_qualified_qwen_route():
    with patch.dict(os.environ, _groq_env(), clear=True):
        profile = role_routing_profile("repair")
        candidates = ModelRegistry().candidates(profile.selector())

    assert candidates
    assert candidates[0].id == "groq:qwen/qwen3.6-27b"
    assert {"text", "coding", "tools"}.issubset(candidates[0].capabilities)


def test_planner_does_not_treat_inconclusive_qwen_planning_as_a_pass():
    with patch.dict(os.environ, _groq_env(), clear=True):
        profile = role_routing_profile("planner")
        candidates = ModelRegistry().candidates(profile.selector())

    assert candidates
    assert candidates[0].id == "groq:openai/gpt-oss-120b"
    qwen = next(item for item in candidates if item.id == "groq:qwen/qwen3.6-27b")
    assert "qualified-planning" not in qwen.tags


def test_model_service_preferences_reuse_measured_tags_for_existing_selector_sort():
    assert qualification_preference_tags("coding", {"fast"}) == frozenset(
        {"qualified-coding"}
    )
    assert qualification_preference_tags("coding", {"reasoning"}) == frozenset(
        {"qualified-coding", "qualified-repair"}
    )
    assert qualification_preference_tags("reasoning", {"heavy"}) == frozenset(
        {"qualified-planning"}
    )
    assert qualification_preference_tags("reasoning", {"reliable"}) == frozenset(
        {"qualified-reasoning"}
    )


def test_future_benchmark_json_is_ingested_without_code_changes():
    configured = {
        "reports": [
            {
                "resourceId": "test-provider:test-model",
                "provider": "test-provider",
                "modelId": "test-model",
                "source": "future-benchmark",
                "cases": [
                    {"name": "availability", "passed": True},
                    {"name": "reasoning", "passed": True},
                    {"name": "coding", "passed": False, "classification": "rate_limited"},
                ],
            }
        ]
    }
    with patch.dict(
        os.environ,
        {"OPERLY_MODEL_QUALIFICATION_JSON": json.dumps(configured)},
        clear=True,
    ):
        profile = qualification_profiles()["test-provider:test-model"]

    assert profile.status("availability") == "pass"
    assert profile.status("reasoning") == "pass"
    assert profile.status("coding") == "inconclusive"
    assert "qualified-reasoning" in profile.routing_tags
    assert "qualified-coding" not in profile.routing_tags
