from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from apps.api.runtime_trace_router import _WORKSPACE_TRACE_SURFACES
from packages.software_projects.coding.execution_loop import _trace_metadata
from packages.runtime_plugins import runner_adapters
from packages.runtime_plugins.runner_adapters import ExternalRunnerAdapter, _safe_runner_payload
from packages.runtime_plugins.sandbox import SandboxFailure
from packages.database.model_trace import redact_trace_value
from packages.solutions.traced_generation_worker import _metadata as worker_trace_metadata


def test_runner_trace_payload_never_persists_transport_grants_or_source_text():
    payload = {
        "submission": {
            "workspaceId": "tenant-1",
            "applicationId": "app-1",
            "serviceBindings": [
                {
                    "semanticName": "identity",
                    "capabilityId": "identity.app_users",
                    "transport": {
                        "gatewayUrl": "https://operly.example/api/runtime/identity",
                        "runtimeToken": "runtime-super-secret",
                        "migrationToken": "migration-super-secret",
                    },
                }
            ],
        },
        "bundle": {
            "manifest": {"schemaVersion": 1},
            "files": [
                {
                    "path": "backend/app.py",
                    "content": "print('source must not be duplicated into the trace')",
                    "generatedBy": "agent_runtime",
                }
            ],
        },
    }

    traced = _safe_runner_payload("POST", "/v1/builds", payload)
    encoded = json.dumps(traced, sort_keys=True)

    assert "runtime-super-secret" not in encoded
    assert "migration-super-secret" not in encoded
    assert "source must not be duplicated" not in encoded
    transport = traced["submission"]["serviceBindings"][0]["transport"]
    assert transport == {
        "configured": True,
        "gatewayHost": "operly.example",
        "runtimeTokenPresent": True,
        "migrationTokenPresent": True,
    }
    file_row = traced["bundle"]["files"][0]
    assert file_row["path"] == "backend/app.py"
    assert file_row["bytes"] > 0
    assert file_row["digest"].startswith("sha256:")


def test_solution_attempt_uses_one_correlation_id_across_worker_and_harness():
    plan = SimpleNamespace(id="plan-1")
    harness = _trace_metadata(
        "tenant-1",
        "user-1",
        plan,
        "solution:11111111-1111-1111-1111-111111111111:software-build:4",
    )
    job = SimpleNamespace(
        solution_id="11111111-1111-1111-1111-111111111111",
        attempt=4,
        tenant_id="tenant-1",
        id="job-1",
    )
    worker = worker_trace_metadata(job, "user-1")

    assert harness["conversation_id"] == worker["conversation_id"]
    assert harness["runtime_run_id"] == worker["runtime_run_id"]
    assert harness["runtime_run_id"].endswith(":attempt:4")
    assert harness["surface"] == worker["surface"] == "solution_generation"


def test_solution_generation_surface_is_workspace_owner_debug_visible():
    assert "solution_generation" in _WORKSPACE_TRACE_SURFACES


def test_trace_redaction_covers_runner_grant_key_shapes():
    redacted = redact_trace_value(
        {
            "runtimeToken": "secret-runtime-token",
            "migrationToken": "secret-migration-token",
            "nested": {"authorization": "Bearer abcdefghijklmnopqrstuvwxyz"},
        }
    )
    assert redacted["runtimeToken"] == "[REDACTED]"
    assert redacted["migrationToken"] == "[REDACTED]"
    assert redacted["nested"]["authorization"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_external_runner_preserves_signed_http_400_evidence(monkeypatch):
    token = "t" * 40
    body = json.dumps({"error": "unsupported service binding: identity.app_users"}).encode()
    signature = hmac.new(token.encode(), body, hashlib.sha256).hexdigest()

    class FakeResponse:
        status = 400
        headers = {"X-Operly-Signature": signature}

        async def read(self):
            return body

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            return FakeResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(runner_adapters, "validate_runner_url", lambda value: value)
    monkeypatch.setattr(runner_adapters.aiohttp, "ClientSession", FakeSession)

    adapter = ExternalRunnerAdapter("https://runner.example", token)
    with pytest.raises(SandboxFailure) as exc:
        await adapter._request("POST", "/v1/builds", {"submission": {}, "bundle": {}})

    assert "status 400" in str(exc.value)
    assert "unsupported service binding: identity.app_users" in str(exc.value)
    assert getattr(exc.value, "status", None) == 400
    assert getattr(exc.value, "response_body", None) == {
        "error": "unsupported service binding: identity.app_users"
    }
