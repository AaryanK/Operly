from packages.coding_harness.model_client import coding_model_client


def _primary_model(client):
    model = client.inner.model
    models = getattr(model, "models", None)
    return models[0] if models else model


def test_coding_route_is_not_restricted_to_gemma_or_ollama(monkeypatch):
    monkeypatch.setenv("OPERLY_MODEL_CODING_PROVIDER", "openrouter")
    monkeypatch.setenv("OPERLY_MODEL_CODING", "stealth/ox-alpha")
    monkeypatch.delenv("OPERLY_MODEL_CODING_CANDIDATES_JSON", raising=False)

    client = coding_model_client("coding")
    primary = _primary_model(client)

    assert primary.provider == "openrouter"
    assert primary.provider_model_id == "stealth/ox-alpha"


def test_coding_route_accepts_arbitrary_registered_provider_and_model(monkeypatch):
    monkeypatch.setenv("OPERLY_MODEL_CODING_PROVIDER", "future-provider")
    monkeypatch.setenv("OPERLY_MODEL_CODING", "future/model")
    monkeypatch.delenv("OPERLY_MODEL_CODING_CANDIDATES_JSON", raising=False)

    client = coding_model_client("coding")
    primary = _primary_model(client)

    assert primary.provider == "future-provider"
    assert primary.provider_model_id == "future/model"
