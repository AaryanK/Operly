from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.runner import DockerIsolationBackend
from packages.app_identity.bindings import attach_app_identity_grants
from packages.app_identity.contracts import (
    APP_IDENTITY_CAPABILITY_ID,
    InvitationCreateRequest,
    RegisterRequest,
)
from packages.app_identity.store import AppIdentityError, AppIdentityStore
from packages.coding_harness.source_service import _plan_specification
from packages.custom_software.runner_contracts import (
    BuildSubmission,
    HealthCheck,
    NetworkPolicy,
    ResourcePolicy,
    ServiceBindingRequest,
)
from packages.relational_data.tokens import verify_capability_grant
from packages.runtime_plugins.app_identity_source_validation import validate_app_identity_source
from packages.workspace_entities.contracts import EntityCreate
from packages.workspace_entities.store import WorkspaceEntityStore

SECRET = "app-identity-test-secret-" + "x" * 48
PASSWORD = "Correct-Horse-Battery-Staple-42!"


@pytest.mark.asyncio
async def test_app_sessions_are_isolated_from_sibling_solutions(tmp_path: Path):
    store = AppIdentityStore(
        f"sqlite+aiosqlite:///{tmp_path / 'identity.db'}",
        token_secret=SECRET,
    )
    try:
        first = await store.register(
            "workspace-a",
            "clock-in",
            RegisterRequest(
                email="person@example.test",
                password=PASSWORD,
                displayName="Person One",
            ),
        )
        verified = await store.verify_session(
            "workspace-a", "clock-in", first["sessionToken"]
        )
        assert verified["user"]["id"] == first["user"]["id"]

        with pytest.raises(AppIdentityError, match="Session is no longer valid"):
            await store.verify_session(
                "workspace-a", "scheduling", first["sessionToken"]
            )

        second = await store.register(
            "workspace-a",
            "scheduling",
            RegisterRequest(
                email="person@example.test",
                password=PASSWORD,
                displayName="Person One",
            ),
        )
        assert second["user"]["id"] != first["user"]["id"]

        await store.logout("workspace-a", "clock-in", first["sessionToken"])
        with pytest.raises(AppIdentityError, match="Session is no longer valid"):
            await store.verify_session(
                "workspace-a", "clock-in", first["sessionToken"]
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_owner_invitation_can_link_login_to_canonical_employee(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'identity.db'}"
    identities = AppIdentityStore(url, token_secret=SECRET)
    entities = WorkspaceEntityStore(url)
    try:
        employee = await entities.create(
            "workspace-a",
            EntityCreate(
                kind="employee",
                values={
                    "display_name": "Ada Lovelace",
                    "email": "ada@example.test",
                },
            ),
        )
        invite = await identities.create_invitation(
            "workspace-a",
            "clock-in",
            InvitationCreateRequest(
                email="ada@example.test",
                displayName="Ada Lovelace",
                role="employee",
                entityKind="employee",
                entityId=employee["id"],
            ),
        )
        accepted = await identities.accept_invitation(
            "workspace-a",
            "clock-in",
            invite["invitationToken"],
            PASSWORD,
        )
        assert accepted["user"]["entityKind"] == "employee"
        assert accepted["user"]["entityId"] == employee["id"]
        assert accepted["user"]["role"] == "employee"

        with pytest.raises(AppIdentityError, match="Invitation is no longer valid"):
            await identities.accept_invitation(
                "workspace-a",
                "clock-in",
                invite["invitationToken"],
                PASSWORD,
            )
    finally:
        await identities.close()
        await entities.close()


def _submission() -> BuildSubmission:
    return BuildSubmission(
        workspaceId="workspace-a",
        applicationId="customer-portal",
        planVersion=1,
        sourceVersion=1,
        stackId="operly-fullstack-v1",
        sourceBundleDigest="sha256:" + "a" * 64,
        operations=[
            "stage_source",
            "static_analysis",
            "build",
            "test",
            "start",
            "health_check",
            "acceptance_test",
        ],
        healthCheck=HealthCheck(),
        resources=ResourcePolicy(previewSeconds=1800),
        installNetwork=NetworkPolicy(mode="none"),
        network=NetworkPolicy(mode="loopback_only"),
        serviceBindings=[
            ServiceBindingRequest(
                semanticName="identity",
                capabilityId=APP_IDENTITY_CAPABILITY_ID,
            )
        ],
        idempotencyKey="app-identity-build-1",
    )


def test_identity_binding_grant_is_scoped_and_runner_archive_row_is_redacted(monkeypatch):
    monkeypatch.setenv("OPERLY_RUNTIME_BINDING_SECRET", SECRET)
    monkeypatch.setenv("OPERLY_APP_IDENTITY_SECRET", SECRET)
    monkeypatch.setenv("OPERLY_APP_IDENTITY_GATEWAY_URL", "https://operly.example")
    monkeypatch.setenv(
        "OPERLY_APP_DATA_DATABASE_URL",
        "sqlite+aiosqlite:///./app-identity-test.db",
    )
    semantic = _submission()
    transported = attach_app_identity_grants(semantic)
    assert semantic.serviceBindings[0].transport is None
    transport = transported.serviceBindings[0].transport
    assert transport is not None
    claims = verify_capability_grant(
        transport.runtimeToken,
        capability_id=APP_IDENTITY_CAPABILITY_ID,
        required_scope="auth",
        allowed_scopes=frozenset({"auth"}),
        secret=SECRET,
    )
    assert claims.workspace_id == "workspace-a"
    assert claims.application_id == "customer-portal"

    backend = object.__new__(DockerIsolationBackend)
    rows = backend._binding_file_rows(transported, "job1")
    encoded = json.dumps(rows)
    assert rows[0]["endpoint"].startswith("http://operly-binding-")
    assert "runtimeToken" not in encoded
    assert transport.runtimeToken not in encoded
    assert "gatewayUrl" not in encoded


def test_identity_source_contract_requires_one_canonical_semantic_name():
    valid = [
        type("F", (), {
            "path": "operly.solution.json",
            "content": json.dumps({
                "schemaVersion": "operly.solution/v1",
                "runtime": "operly-fullstack-v1",
                "runtimeVersion": 1,
                "bindings": [
                    {"semanticName": "identity", "capabilityId": APP_IDENTITY_CAPABILITY_ID}
                ],
            }).encode(),
        })()
    ]
    assert validate_app_identity_source(valid).valid

    invalid = [
        type("F", (), {
            "path": "operly.solution.json",
            "content": json.dumps({
                "schemaVersion": "operly.solution/v1",
                "runtime": "operly-fullstack-v1",
                "runtimeVersion": 1,
                "bindings": [
                    {"semanticName": "auth", "capabilityId": APP_IDENTITY_CAPABILITY_ID}
                ],
            }).encode(),
        })()
    ]
    result = validate_app_identity_source(invalid)
    assert not result.valid
    assert any("semanticName must be identity" in error for error in result.errors)


def test_identity_runtime_routes_are_mounted_under_single_api_prefix():
    from apps.api.app_identity_router import admin_router, runtime_router
    from apps.api.main import app
    from apps.api.workspace_entities_router import router as entity_router

    runtime_paths = sorted(getattr(route, "path", "") for route in runtime_router.routes)
    admin_paths = sorted(getattr(route, "path", "") for route in admin_router.routes)
    entity_paths = sorted(getattr(route, "path", "") for route in entity_router.routes)
    app_paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/runtime/app-identity/register" in runtime_paths, runtime_paths
    assert "/api/app-identities/{application_id}/invitations" in admin_paths, admin_paths
    assert "/api/runtime/entities/schema" in entity_paths, entity_paths

    diagnostics = {
        "runtime_router": runtime_paths,
        "admin_router": admin_paths,
        "entity_router": entity_paths,
        "app_identity_paths": sorted(
            path for path in app_paths if "identity" in path or "entities" in path
        ),
    }
    assert "/api/runtime/app-identity/register" in app_paths, diagnostics
    assert "/api/runtime/app-identity/login" in app_paths, diagnostics
    assert "/api/app-identities/{application_id}/invitations" in app_paths, diagnostics
    assert "/api/runtime/entities/schema" in app_paths, diagnostics
    assert "/api/api/runtime/app-identity/register" not in app_paths, diagnostics


def test_coding_specification_teaches_generated_apps_the_identity_binding():
    specification = json.loads(
        _plan_specification(
            {
                "projectName": "Customer Portal",
                "summary": "Customers can sign in to view their orders.",
                "requirementLedger": [],
                "planTree": [],
            }
        )
    )
    guidance = specification["operlyExecutionContract"]["appIdentity"]
    assert "identity.app_users" in guidance
    assert "semanticName: identity" in guidance
    assert "/login" in guidance and "/session" in guidance
    assert "Operly account" in guidance
