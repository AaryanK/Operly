import pytest

from packages.coding_harness.model_client import _assert_coding_route_authorized
from packages.model_runtime.portfolio import ModelRoute


def test_default_gemma_coding_route_is_authorized(monkeypatch):
    monkeypatch.delenv("OPERLY_CODING_ALLOWED_MODELS", raising=False)
    _assert_coding_route_authorized("coding", ModelRoute("ollama", "gemma4:31b"))


def test_unapproved_coding_fallback_is_rejected(monkeypatch):
    monkeypatch.delenv("OPERLY_CODING_ALLOWED_MODELS", raising=False)
    route = ModelRoute("ollama", "gemma4:31b", ("nemotron-3-ultra",))
    with pytest.raises(RuntimeError, match="not owner-authorized"):
        _assert_coding_route_authorized("coding", route)


def test_owner_can_explicitly_authorize_additional_coding_model(monkeypatch):
    monkeypatch.setenv("OPERLY_CODING_ALLOWED_MODELS", "gemma4:31b,nemotron-3-ultra")
    route = ModelRoute("ollama", "gemma4:31b", ("nemotron-3-ultra",))
    _assert_coding_route_authorized("coding", route)


def test_non_coding_routes_are_not_changed_by_coding_allowlist(monkeypatch):
    monkeypatch.delenv("OPERLY_CODING_ALLOWED_MODELS", raising=False)
    _assert_coding_route_authorized("business_agent", ModelRoute("ollama", "nemotron-3-ultra"))
