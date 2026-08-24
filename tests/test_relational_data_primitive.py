from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.relational_data.bindings import attach_transport_grants
from packages.relational_data.contracts import (
    DeleteRequest,
    FilterClause,
    InsertRequest,
    QueryRequest,
    RelationalMigration,
    UpdateRequest,
)
from packages.relational_data.store import RelationalDataError, RelationalDataStore
from packages.relational_data.tokens import BindingGrantError, issue_binding_grant, verify_binding_grant
from packages.custom_software.runner_contracts import ResourcePolicy, ServiceBindingRequest


SECRET = "relational-test-secret-" + "x" * 48


def migration(version=1, *, extra=False):
    operations = [
        {
            "op": "create_table",
            "table": "employees",
            "columns": [
                {"name": "id", "type": "uuid", "nullable": False, "primaryKey": True},
                {"name": "name", "type": "string", "nullable": False},
                {"name": "active", "type": "boolean", "nullable": False},
                {"name": "profile", "type": "json"},
            ],
        }
    ]
    if extra:
        operations.append(
            {
                "op": "create_index",
                "table": "employees",
                "name": "employees_name",
                "columns": ["name"],
            }
        )
    return RelationalMigration.model_validate(
        {
            "schemaVersion": "operly.relational.migration/v1",
            "version": version,
            "name": "initial employees",
            "operations": operations,
        }
    )


@pytest.mark.asyncio
async def test_workspace_application_namespaces_are_physically_isolated(tmp_path: Path):
    store = RelationalDataStore(f"sqlite+aiosqlite:///{tmp_path / 'apps.db'}")
    try:
        await store.apply_migrations("workspace-a", "app-one", [migration()])
        await store.apply_migrations("workspace-a", "app-two", [migration()])
        await store.apply_migrations("workspace-b", "app-one", [migration()])

        await store.insert(
            "workspace-a",
            "app-one",
            InsertRequest(table="employees", values={"id": "e-1", "name": "A", "active": True, "profile": {"team": "x"}}),
        )
        await store.insert(
            "workspace-a",
            "app-two",
            InsertRequest(table="employees", values={"id": "e-2", "name": "B", "active": True}),
        )

        one = await store.query("workspace-a", "app-one", QueryRequest(table="employees"))
        two = await store.query("workspace-a", "app-two", QueryRequest(table="employees"))
        other_workspace = await store.query("workspace-b", "app-one", QueryRequest(table="employees"))
        assert [row["id"] for row in one["rows"]] == ["e-1"]
        assert one["rows"][0]["profile"] == {"team": "x"}
        assert [row["id"] for row in two["rows"]] == ["e-2"]
        assert other_workspace["rows"] == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_migrations_are_idempotent_but_checksum_drift_fails(tmp_path: Path):
    store = RelationalDataStore(f"sqlite+aiosqlite:///{tmp_path / 'apps.db'}")
    try:
        first = await store.apply_migrations("w", "a", [migration()])
        repeated = await store.apply_migrations("w", "a", [migration()])
        assert first == {"currentVersion": 1, "appliedVersions": [1]}
        assert repeated == {"currentVersion": 1, "appliedVersions": []}
        with pytest.raises(RelationalDataError, match="checksum changed"):
            await store.apply_migrations("w", "a", [migration(extra=True)])
        with pytest.raises(RelationalDataError, match="expected version 2"):
            await store.apply_migrations(
                "w",
                "a",
                [RelationalMigration.model_validate({
                    "schemaVersion": "operly.relational.migration/v1",
                    "version": 3,
                    "name": "skip",
                    "operations": [{"op": "create_table", "table": "later", "columns": [{"name": "id", "type": "uuid", "primaryKey": True, "nullable": False}]}],
                })],
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_runtime_mutations_are_typed_and_mass_write_is_not_implicit(tmp_path: Path):
    store = RelationalDataStore(f"sqlite+aiosqlite:///{tmp_path / 'apps.db'}")
    try:
        await store.apply_migrations("w", "a", [migration()])
        await store.insert("w", "a", InsertRequest(table="employees", values={"id": "1", "name": "One", "active": True}))
        await store.insert("w", "a", InsertRequest(table="employees", values={"id": "2", "name": "Two", "active": False}))
        updated = await store.update(
            "w",
            "a",
            UpdateRequest(
                table="employees",
                values={"active": False},
                filters=[FilterClause(column="id", op="eq", value="1")],
            ),
        )
        assert updated["updated"] == 1
        result = await store.query(
            "w",
            "a",
            QueryRequest(
                table="employees",
                columns=["id", "active"],
                filters=[FilterClause(column="active", op="eq", value=False)],
                orderBy=[{"column": "id", "direction": "asc"}],
            ),
        )
        assert result["rows"] == [{"id": "1", "active": False}, {"id": "2", "active": False}]
        deleted = await store.delete(
            "w",
            "a",
            DeleteRequest(table="employees", filters=[FilterClause(column="id", value="2")]),
        )
        assert deleted["deleted"] == 1
        with pytest.raises(Exception):
            UpdateRequest(table="employees", values={"active": True}, filters=[])
        with pytest.raises(Exception):
            DeleteRequest(table="employees", filters=[])
    finally:
        await store.close()


def test_binding_grants_are_scoped_expiring_and_permissioned():
    token = issue_binding_grant("workspace", "application", scopes=("read", "write"), secret=SECRET, ttl_seconds=120)
    claims = verify_binding_grant(token, required_scope="read", secret=SECRET)
    assert claims.workspace_id == "workspace"
    assert claims.application_id == "application"
    assert set(claims.scopes) == {"read", "write"}
    with pytest.raises(BindingGrantError, match="does not authorize"):
        verify_binding_grant(token, required_scope="migrate", secret=SECRET)
    with pytest.raises(BindingGrantError, match="expired"):
        verify_binding_grant(token, required_scope="read", secret=SECRET, now=claims.expires_at)


def test_transport_grants_do_not_mutate_semantic_submission(monkeypatch):
    monkeypatch.setenv("OPERLY_RUNTIME_BINDING_SECRET", SECRET)
    monkeypatch.setenv("OPERLY_RELATIONAL_GATEWAY_URL", "https://operly.example")
    monkeypatch.setenv("OPERLY_APP_DATA_DATABASE_URL", "sqlite+aiosqlite:///./relational-test.db")
    submission = SimpleNamespace(
        workspaceId="workspace",
        applicationId="application",
        resources=ResourcePolicy(previewSeconds=1800),
        serviceBindings=[ServiceBindingRequest(semanticName="data", capabilityId="data.relational")],
        model_copy=lambda **_: None,
    )
    # Use the real BuildSubmission model for copy semantics after proving the request
    # itself has no secret-bearing transport.
    from packages.custom_software.runner_contracts import BuildSubmission, HealthCheck, NetworkPolicy
    real = BuildSubmission(
        workspaceId="workspace",
        applicationId="application",
        planVersion=1,
        sourceVersion=1,
        stackId="operly-fullstack-v1",
        sourceBundleDigest="sha256:" + "a" * 64,
        operations=["stage_source", "static_analysis", "build", "test", "start", "health_check", "acceptance_test"],
        healthCheck=HealthCheck(),
        resources=ResourcePolicy(previewSeconds=1800),
        installNetwork=NetworkPolicy(mode="none"),
        network=NetworkPolicy(mode="loopback_only"),
        serviceBindings=[ServiceBindingRequest(semanticName="data", capabilityId="data.relational")],
        idempotencyKey="relational-build-1",
    )
    transported = attach_transport_grants(real)
    assert real.serviceBindings[0].transport is None
    assert transported.serviceBindings[0].transport is not None
    assert "runtimeToken" not in real.model_dump_json()
    assert "migrationToken" not in real.model_dump_json()
