from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.custom_software.runner_contracts import BuildSubmission, HealthCheck, NetworkPolicy, ResourcePolicy, ServiceBindingRequest
from packages.custom_software.source_bundles import SourceFile
from packages.relational_data.tokens import BindingGrantError, issue_capability_grant, verify_capability_grant
from packages.workspace_entities.bindings import attach_workspace_entity_grants
from packages.workspace_entities.contracts import EntityCreate, EntityList, WORKSPACE_ENTITY_CAPABILITY_ID
from packages.workspace_entities.manifest import validate_workspace_entity_source
from packages.workspace_entities.store import WorkspaceEntityStore

SECRET = "workspace-entity-test-secret-" + "x" * 48


def _grant(workspace: str, application: str, kind: str, *, secret: str = SECRET) -> str:
    return issue_capability_grant(
        workspace,
        application,
        capability_id=WORKSPACE_ENTITY_CAPABILITY_ID,
        scopes=("read", "write"),
        allowed_scopes=frozenset({"read", "write"}),
        resources=(kind,),
        ttl_seconds=300,
        secret=secret,
    )


@pytest.mark.asyncio
async def test_two_solutions_share_one_employee_identity_but_other_workspace_isolated(tmp_path: Path):
    store = WorkspaceEntityStore(f"sqlite+aiosqlite:///{tmp_path / 'entities.db'}")
    try:
        clock_claims = verify_capability_grant(
            _grant("workspace-a", "clock-in", "employee"),
            capability_id=WORKSPACE_ENTITY_CAPABILITY_ID,
            required_scope="write",
            allowed_scopes=frozenset({"read", "write"}),
            required_resource="employee",
            secret=SECRET,
        )
        created = await store.create(
            clock_claims.workspace_id,
            EntityCreate(kind="employee", values={"display_name": "Ada Lovelace", "email": "ada@example.test"}),
        )

        schedule_claims = verify_capability_grant(
            _grant("workspace-a", "scheduling", "employee"),
            capability_id=WORKSPACE_ENTITY_CAPABILITY_ID,
            required_scope="read",
            allowed_scopes=frozenset({"read", "write"}),
            required_resource="employee",
            secret=SECRET,
        )
        rows = await store.list(schedule_claims.workspace_id, EntityList(kind="employee"))
        assert len(rows["rows"]) == 1
        assert rows["rows"][0]["id"] == created["id"]
        assert rows["rows"][0]["display_name"] == "Ada Lovelace"

        other = await store.list("workspace-b", EntityList(kind="employee"))
        assert other["rows"] == []
    finally:
        await store.close()


def test_binding_grant_is_resource_scoped_to_one_canonical_kind():
    token = _grant("workspace", "clock-in", "employee")
    claims = verify_capability_grant(
        token,
        capability_id=WORKSPACE_ENTITY_CAPABILITY_ID,
        required_scope="read",
        allowed_scopes=frozenset({"read", "write"}),
        required_resource="employee",
        secret=SECRET,
    )
    assert claims.resources == ("employee",)
    with pytest.raises(BindingGrantError, match="resource"):
        verify_capability_grant(
            token,
            capability_id=WORKSPACE_ENTITY_CAPABILITY_ID,
            required_scope="read",
            allowed_scopes=frozenset({"read", "write"}),
            required_resource="customer",
            secret=SECRET,
        )


def _source(*, private_employee_table: bool = False):
    solution = {
        "schemaVersion": "operly.solution/v1",
        "runtime": "operly-fullstack-v1",
        "runtimeVersion": 1,
        "bindings": [
            {"semanticName": "employee", "capabilityId": WORKSPACE_ENTITY_CAPABILITY_ID, "required": True}
        ],
    }
    entities = {
        "schemaVersion": "operly.workspace-entities/v1",
        "entities": [{"semanticName": "employee", "kind": "employee", "access": ["read", "write"]}],
    }
    files = [
        SourceFile("operly.solution.json", json.dumps(solution).encode(), "test"),
        SourceFile("operly.entities.json", json.dumps(entities).encode(), "test"),
    ]
    if private_employee_table:
        migration = {
            "schemaVersion": "operly.relational.migration/v1",
            "version": 1,
            "name": "bad employee silo",
            "operations": [{
                "op": "create_table",
                "table": "employees",
                "columns": [{"name": "id", "type": "uuid", "nullable": False, "primaryKey": True}],
            }],
        }
        files.append(SourceFile("migrations/001.json", json.dumps(migration).encode(), "test"))
    return files


def test_source_contract_prevents_canonical_employee_shadow_table():
    valid = validate_workspace_entity_source(_source())
    assert valid.valid, valid.errors
    invalid = validate_workspace_entity_source(_source(private_employee_table=True))
    assert not invalid.valid
    assert any("do not create app-private table employees" in message for message in invalid.errors)


def test_transport_grants_are_scoped_per_semantic_entity_binding(monkeypatch):
    monkeypatch.setenv("OPERLY_RUNTIME_BINDING_SECRET", SECRET)
    monkeypatch.setenv("OPERLY_ENTITY_GATEWAY_URL", "https://operly.example")
    monkeypatch.setenv("OPERLY_APP_DATA_DATABASE_URL", "sqlite+aiosqlite:///./workspace-entity-test.db")
    submission = BuildSubmission(
        workspaceId="workspace",
        applicationId="scheduling",
        planVersion=1,
        sourceVersion=1,
        stackId="operly-fullstack-v1",
        sourceBundleDigest="sha256:" + "a" * 64,
        operations=["stage_source", "static_analysis", "build", "test", "start", "health_check", "acceptance_test"],
        healthCheck=HealthCheck(),
        resources=ResourcePolicy(previewSeconds=1800),
        installNetwork=NetworkPolicy(mode="none"),
        network=NetworkPolicy(mode="loopback_only"),
        serviceBindings=[
            ServiceBindingRequest(semanticName="employee", capabilityId=WORKSPACE_ENTITY_CAPABILITY_ID),
            ServiceBindingRequest(semanticName="location", capabilityId=WORKSPACE_ENTITY_CAPABILITY_ID),
        ],
        idempotencyKey="workspace-entity-build-1",
    )
    transported = attach_workspace_entity_grants(submission)
    assert all(item.transport is None for item in submission.serviceBindings)
    resources = []
    for binding in transported.serviceBindings:
        claims = verify_capability_grant(
            binding.transport.runtimeToken,
            capability_id=WORKSPACE_ENTITY_CAPABILITY_ID,
            required_scope="read",
            allowed_scopes=frozenset({"read", "write"}),
            required_resource=binding.semanticName,
            secret=SECRET,
        )
        resources.append(claims.resources)
    assert resources == [("employee",), ("location",)]
