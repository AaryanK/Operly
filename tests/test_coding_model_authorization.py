from unittest.mock import patch

from packages.coding_harness.model_client import coding_model_client
from packages.model_runtime.portfolio import ModelRoute


def test_coding_route_is_not_restricted_to_gemma_or_ollama(monkeypatch):
    monkeypatch.setenv("OPERLY_MODEL_CODING_PROVIDER", "openrouter")
    monkeypatch.setenv("OPERLY_MODEL_CODING", "stealth/ox-alpha")

    fake_client = object()
    with patch(
        "packages.coding_harness.model_client.model_client_for_route",
        return_value=fake_client,
    ) as factory, patch(
        "packages.coding_harness.model_client.ContextBoundCodingClient",
        side_effect=lambda client: client,
    ):
        client = coding_model_client("coding")

    assert client is fake_client
    assert factory.call_args.args[0] == ModelRoute("openrouter", "stealth/ox-alpha")


def test_coding_route_accepts_arbitrary_registered_provider_and_model(monkeypatch):
    monkeypatch.setenv("OPERLY_MODEL_CODING_PROVIDER", "future-provider")
    monkeypatch.setenv("OPERLY_MODEL_CODING", "future/model")

    fake_client = object()
    with patch(
        "packages.coding_harness.model_client.model_client_for_route",
        return_value=fake_client,
    ) as factory, patch(
        "packages.coding_harness.model_client.ContextBoundCodingClient",
        side_effect=lambda client: client,
    ):
        client = coding_model_client("coding")

    assert client is fake_client
    assert factory.call_args.args[0] == ModelRoute("future-provider", "future/model")
