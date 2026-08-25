from __future__ import annotations

import io
import json
import tarfile

from packages.runtime_plugins.runner_contracts import (
    BuildSubmission,
    HealthCheck,
    NetworkPolicy,
    ServiceBindingRequest,
    ServiceBindingTransport,
)
from packages.software_projects.source_bundle import SourceFile, build_bundle
from packages.runtime_plugins.relational_source_validation import validate_relational_source


def _submission():
    return BuildSubmission(
        workspaceId="workspace-a",
        applicationId="application-a",
        planVersion=1,
        sourceVersion=1,
        stackId="operly-fullstack-v1",
        sourceBundleDigest="sha256:" + "a" * 64,
        operations=["stage_source", "static_analysis", "build", "test", "start", "health_check", "acceptance_test"],
        healthCheck=HealthCheck(),
        installNetwork=NetworkPolicy(mode="none"),
        network=NetworkPolicy(mode="loopback_only"),
        serviceBindings=[
            ServiceBindingRequest(
                semanticName="data",
                capabilityId="data.relational",
                transport=ServiceBindingTransport(
                    gatewayUrl="https://operly.example",
                    runtimeToken="runtime-" + "x" * 48,
                    migrationToken="migration-" + "y" * 48,
                ),
            )
        ],
        idempotencyKey="relational-runner-test",
    )


def _bundle(with_binding=True, migration_version=1):
    manifest = {
        "schemaVersion": "operly.solution/v1",
        "runtime": "operly-fullstack-v1",
        "runtimeVersion": 1,
        "dependencies": [],
        "bindings": ([{"semanticName": "data", "capabilityId": "data.relational"}] if with_binding else []),
    }
    migration = {
        "schemaVersion": "operly.relational.migration/v1",
        "version": migration_version,
        "name": "employees",
        "operations": [
            {
                "op": "create_table",
                "table": "employees",
                "columns": [{"name": "id", "type": "uuid", "nullable": False, "primaryKey": True}],
            }
        ],
    }
    files = [
        SourceFile("operly.solution.json", json.dumps(manifest).encode(), "test"),
        SourceFile("frontend/index.html", b"ok", "test"),
        SourceFile("backend/app.py", b"print('ok')", "test"),
        SourceFile("tests/test_app.py", b"import unittest\n", "test"),
        SourceFile("migrations/001_employees.json", json.dumps(migration).encode(), "test"),
    ]
    return build_bundle(files, "workspace-a", "application-a", "plan-a", 1, 1, "sha256:" + "0" * 64)


def test_generated_binding_archive_contains_endpoint_but_never_transport_secret():
    from apps.runner.docker_backend import DockerIsolationBackend

    backend = object.__new__(DockerIsolationBackend)
    payload = backend._archive(_bundle(), _submission(), "job123")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r") as archive:
        raw = archive.extractfile(".operly-bindings.json").read()
    text = raw.decode()
    bindings = json.loads(text)
    assert bindings == [
        {
            "capabilityId": "data.relational",
            "endpoint": "http://operly-binding-job123-data:8083",
            "required": True,
            "semanticName": "data",
        }
    ]
    assert "runtime-" not in text
    assert "migration-" not in text
    assert "gatewayUrl" not in text
    assert "transport" not in text


def test_migrations_require_relational_binding():
    validation = validate_relational_source(_bundle(with_binding=False))
    assert not validation.valid
    assert any("require a data.relational" in error for error in validation.errors)


def test_relational_migration_history_must_be_contiguous():
    validation = validate_relational_source(_bundle(migration_version=2))
    assert not validation.valid
    assert any("contiguous" in error for error in validation.errors)
