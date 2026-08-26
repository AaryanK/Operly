from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from apps.runner.dev_main import app
from packages.custom_software.sandbox import SandboxUnavailable, validate_runner_url


TOKEN = "local-development-runner-token-0123456789abcdef"


def _enable_local_sidecar(monkeypatch):
    monkeypatch.setenv("OPERLY_ENV", "development")
    monkeypatch.setenv("OPERLY_ENABLE_LOCAL_RUNNER_SIDECAR", "1")
    monkeypatch.setenv("OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER", "1")
    monkeypatch.setenv("OPERLY_SANDBOX_RUNNER_TOKEN", TOKEN)
    monkeypatch.setenv("OPERLY_LOCAL_RUNNER_PUBLIC_BASE_URL", "http://127.0.0.1:8091")


def _signed_headers(raw: bytes) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-Operly-Signature": hmac.new(TOKEN.encode(), raw, hashlib.sha256).hexdigest(),
        "Content-Type": "application/json",
    }


def test_runner_url_allows_only_explicit_loopback_sidecar_in_development(monkeypatch):
    _enable_local_sidecar(monkeypatch)
    assert validate_runner_url("http://127.0.0.1:8091") == "http://127.0.0.1:8091"
    assert validate_runner_url("http://localhost:8091/") == "http://localhost:8091"

    with pytest.raises(SandboxUnavailable):
        validate_runner_url("http://192.168.1.10:8091")
    with pytest.raises(SandboxUnavailable):
        validate_runner_url("http://10.0.0.5:8091")
    with pytest.raises(SandboxUnavailable):
        validate_runner_url("http://127.0.0.1:8091/base")
    with pytest.raises(SandboxUnavailable):
        validate_runner_url("http://127.0.0.1:8091?debug=1")
    with pytest.raises(SandboxUnavailable):
        validate_runner_url("http://127.0.0.1:8091#fragment")


def test_runner_url_loopback_exception_is_impossible_in_production(monkeypatch):
    monkeypatch.setenv("OPERLY_ENV", "production")
    monkeypatch.setenv("OPERLY_ENABLE_LOCAL_RUNNER_SIDECAR", "1")
    monkeypatch.setenv("OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER", "1")

    with pytest.raises(SandboxUnavailable):
        validate_runner_url("http://127.0.0.1:8091")
    with pytest.raises(SandboxUnavailable):
        validate_runner_url("https://127.0.0.1:8091")


def test_runner_url_requires_explicit_sidecar_opt_in(monkeypatch):
    monkeypatch.setenv("OPERLY_ENV", "development")
    monkeypatch.delenv("OPERLY_ENABLE_LOCAL_RUNNER_SIDECAR", raising=False)

    with pytest.raises(SandboxUnavailable):
        validate_runner_url("http://127.0.0.1:8091")


def test_dev_sidecar_speaks_signed_external_runner_capabilities_protocol(monkeypatch):
    _enable_local_sidecar(monkeypatch)
    raw = b"{}"
    client = TestClient(app)
    response = client.request(
        "GET",
        "/v1/capabilities",
        content=raw,
        headers=_signed_headers(raw),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["protocolVersion"] >= 1
    assert "operly-fullstack-v1" in payload["profiles"]
    expected = hmac.new(TOKEN.encode(), response.content, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(response.headers["X-Operly-Signature"], expected)


def test_dev_sidecar_rejects_non_origin_public_base_url(monkeypatch):
    _enable_local_sidecar(monkeypatch)
    monkeypatch.setenv("OPERLY_LOCAL_RUNNER_PUBLIC_BASE_URL", "http://127.0.0.1:8091/base")

    response = TestClient(app).get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_dev_sidecar_refuses_to_become_ready_in_production(monkeypatch):
    monkeypatch.setenv("OPERLY_ENV", "production")
    monkeypatch.setenv("OPERLY_ENABLE_LOCAL_RUNNER_SIDECAR", "1")
    monkeypatch.setenv("OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER", "1")
    monkeypatch.setenv("OPERLY_SANDBOX_RUNNER_TOKEN", TOKEN)

    response = TestClient(app).get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
