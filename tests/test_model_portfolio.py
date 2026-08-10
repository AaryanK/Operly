import os
from unittest.mock import patch

from packages.model_runtime.portfolio import configured_portfolio, model_route


def test_default_portfolio_assigns_every_allowed_model_to_a_role():
    with patch.dict(os.environ, {}, clear=True):
        portfolio = configured_portfolio()

    configured = {
        model
        for route in portfolio.values()
        for model in [route["primary"], *route["fallbacks"]]
    }
    assert {
        "gpt-oss:120b",
        "gpt-oss:20b",
        "gemma4:31b",
        "nemotron-3-nano:30b",
        "nemotron-3-super",
        "nemotron-3-ultra",
        "minimax-m3",
    } <= configured


def test_role_route_supports_provider_and_model_overrides():
    with patch.dict(os.environ, {
        "OPERLY_MODEL_CODING_PROVIDER": "openrouter",
        "OPERLY_MODEL_CODING": "vendor/coding-model",
        "OPERLY_MODEL_CODING_FALLBACKS": "vendor/fallback-one,vendor/fallback-two",
    }, clear=False):
        route = model_route("coding")

    assert route.provider == "openrouter"
    assert route.primary == "vendor/coding-model"
    assert route.fallbacks == ("vendor/fallback-one", "vendor/fallback-two")


def test_planning_and_validation_use_independent_primary_models():
    with patch.dict(os.environ, {}, clear=True):
        assert model_route("planner").primary == "nemotron-3-ultra"
        assert model_route("global_validator").primary == "gpt-oss:120b"
        assert model_route("coding").primary == "minimax-m3"
        assert model_route("repair").primary == "nemotron-3-super"
