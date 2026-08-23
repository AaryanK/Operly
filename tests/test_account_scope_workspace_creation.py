from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.dependencies import AccountAuthContext
from apps.api.schemas import WorkspaceCreateInput
from apps.api.workspace_router import create_workspace
from packages.assets import service as asset_service
from packages.capabilities.personal_provider import PersonalRuntimeProvider
from packages.database.db import Base
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models


@pytest.mark.asyncio
async def test_zero_workspace_account_can_create_first_workspace():
    import_all_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        async with sessions() as db:
            user = AppUser(
                email="personal@example.com",
                display_name="Personal User",
                password_hash="test-only",
                active=True,
            )
            db.add(user)
            await db.commit()
            auth = AccountAuthContext(user=user, session=SimpleNamespace(tenant_id=None))

            result = await create_workspace(
                WorkspaceCreateInput(name="Explicit Workspace", timezone="UTC"),
                auth,
                db,
            )

            assert result["name"] == "Explicit Workspace"
            assert result["current"] is False
            assert await db.scalar(select(func.count(Tenant.id))) == 1
            membership = await db.scalar(
                select(TenantMember).where(
                    TenantMember.user_id == user.id,
                    TenantMember.tenant_id == result["id"],
                )
            )
            assert membership is not None
            assert membership.role == "owner"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_personal_ai_can_create_workspace_without_becoming_a_workspace_agent():
    import_all_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        async with sessions() as db:
            user = AppUser(
                email="account-root@example.com",
                display_name="Account Root",
                password_hash="test-only",
                active=True,
            )
            db.add(user)
            await db.flush()
            provider = PersonalRuntimeProvider()
            context = SimpleNamespace(
                actor_id=user.id,
                tenant_id=None,
                db=db,
                invocation={"channel": "web", "metadata": {"is_direct": True, "personal_scope": True}},
            )

            result = await provider.execute(
                context,
                "account.create_workspace",
                {"name": "Street Corner Shop", "timezone": "Africa/Lagos"},
            )
            await db.commit()

            assert result.success is True
            assert result.changed is True
            workspace = await db.get(Tenant, result.external_reference)
            assert workspace is not None
            assert workspace.name == "Street Corner Shop"
            assert workspace.timezone == "Africa/Lagos"
            membership = await db.scalar(
                select(TenantMember).where(
                    TenantMember.user_id == user.id,
                    TenantMember.tenant_id == workspace.id,
                )
            )
            assert membership is not None
            assert membership.role == "owner"
            assert context.tenant_id is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_personal_ai_workspace_settings_follow_real_member_authority():
    import_all_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        async with sessions() as db:
            owner = AppUser(email="owner@example.com", display_name="Owner", password_hash="x", active=True)
            manager = AppUser(email="manager@example.com", display_name="Manager", password_hash="x", active=True)
            workspace = Tenant(name="Global Workspace", slug="global-workspace", timezone="UTC")
            db.add_all([owner, manager, workspace])
            await db.flush()
            db.add_all(
                [
                    TenantMember(tenant_id=workspace.id, user_id=owner.id, role="owner"),
                    TenantMember(tenant_id=workspace.id, user_id=manager.id, role="manager"),
                ]
            )
            await db.flush()
            provider = PersonalRuntimeProvider()

            manager_context = SimpleNamespace(
                actor_id=manager.id,
                tenant_id=None,
                db=db,
                invocation={"channel": "web", "metadata": {"is_direct": True, "personal_scope": True}},
            )
            denied = await provider.execute(
                manager_context,
                "account.update_workspace",
                {"workspace": workspace.id, "name": "Manager Rename"},
            )
            assert denied.success is False
            assert denied.evidence["reason"] == "workspace_settings_permission_denied"
            assert workspace.name == "Global Workspace"

            owner_context = SimpleNamespace(
                actor_id=owner.id,
                tenant_id=None,
                db=db,
                invocation={"channel": "web", "metadata": {"is_direct": True, "personal_scope": True}},
            )
            allowed = await provider.execute(
                owner_context,
                "account.update_workspace",
                {"workspace": workspace.id, "name": "Owner Rename", "timezone": "Asia/Kathmandu"},
            )
            assert allowed.success is True
            assert allowed.changed is True
            assert workspace.name == "Owner Rename"
            assert workspace.timezone == "Asia/Kathmandu"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_personal_ai_delegated_execution_cannot_recurse_into_account_capabilities():
    import_all_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        async with sessions() as db:
            user = AppUser(email="owner@example.com", display_name="Owner", password_hash="x", active=True)
            workspace = Tenant(name="Authorized Workspace", slug="authorized-workspace", timezone="UTC")
            db.add_all([user, workspace])
            await db.flush()
            db.add(TenantMember(tenant_id=workspace.id, user_id=user.id, role="owner"))
            await db.flush()
            provider = PersonalRuntimeProvider()
            context = SimpleNamespace(
                actor_id=user.id,
                tenant_id=None,
                db=db,
                invocation={"channel": "web", "metadata": {"is_direct": True, "personal_scope": True}},
            )

            result = await provider.execute(
                context,
                "account.workspace_execute",
                {
                    "workspace": workspace.id,
                    "capability_id": "account.create_workspace",
                    "arguments": {"name": "Privilege Loop"},
                },
            )
            assert result.success is False
            assert result.evidence["reason"] == "recursive_account_capability_not_allowed"
            assert await db.scalar(select(func.count(Tenant.id))) == 1
    finally:
        await engine.dispose()


def test_workspace_icons_are_first_party_validated_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_service, "ASSET_ROOT", tmp_path.resolve())
    data = b"\x89PNG\r\n\x1a\n" + b"operly-icon"

    stored = asset_service.store_workspace_icon(
        tenant_id="workspace-123",
        data=data,
        declared_content_type="image/png",
    )

    assert stored.content_type == "image/png"
    assert stored.path.read_bytes() == data
    assert asset_service.workspace_icon_path(tenant_id="workspace-123", key=stored.key) == stored.path
    assert tmp_path.resolve() in stored.path.parents

    asset_service.remove_workspace_icon(tenant_id="workspace-123", key=stored.key)
    assert not stored.path.exists()


def test_workspace_icon_rejects_mismatched_content_type(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_service, "ASSET_ROOT", tmp_path.resolve())
    with pytest.raises(TypeError, match="JPEG, PNG, and WebP"):
        asset_service.store_workspace_icon(
            tenant_id="workspace-123",
            data=b"\x89PNG\r\n\x1a\n" + b"not-a-jpeg",
            declared_content_type="image/jpeg",
        )


def test_personal_connector_router_is_registered_in_application_shell():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "apps" / "api" / "main.py").read_text()
    assert "personal_connectors_router" in source
    assert "personal_connectors_router," in source