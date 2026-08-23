from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.dependencies import AccountAuthContext
from apps.api.schemas import WorkspaceCreateInput
from apps.api.workspace_router import create_workspace
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


def test_personal_connector_router_is_registered_in_application_shell():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "apps" / "api" / "main.py").read_text()
    assert "personal_connectors_router" in source
    assert "personal_connectors_router," in source
