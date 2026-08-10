import os
from unittest.mock import patch

from packages.model_runtime.portfolio import configured_portfolio, model_route


def test_default_portfolio_only_uses_the_efficiency_baseline():
    with patch.dict(os.environ, {}, clear=True):
        portfolio = configured_portfolio()

    configured = {
        model
        for route in portfolio.values()
        for model in [route["primary"], *route["fallbacks"]]
    }
    assert configured == {"gemma4:31b"}


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


def test_default_portfolio_uses_gemma_without_automatic_fallbacks():
    with patch.dict(os.environ, {}, clear=True):
        for route in configured_portfolio().values():
            assert route["primary"] == "gemma4:31b"
            assert route["fallbacks"] == []
