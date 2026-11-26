"""Staged identity handoff leaves unmapped users and business authority intact."""
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from packages.database.db import Base
from packages.database.models import AppUser
from packages.database.federated_identity_models import FederatedIdentityMap
from packages.security.identity_handoff import local_identity_allowed
from packages.security.federated_identity import resolve_federated_user,FederationDenied

ISSUER="https://api.dragonzpyder.xyz"


@pytest.mark.asyncio
async def test_pending_active_disabled_handoff_and_unmapped_isolation(monkeypatch):
    engine=create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all,tables=[
                AppUser.__table__,FederatedIdentityMap.__table__])
        async with async_sessionmaker(engine,expire_on_commit=False)() as db:
            a=AppUser(id="synthetic-a",email="a@example.test",display_name="A",active=True)
            b=AppUser(id="synthetic-b",email="b@example.test",display_name="B",active=True)
            db.add_all([a,b])
            await db.commit()
            mapping=FederatedIdentityMap(issuer=ISSUER,subject=a.id,operly_user_id=a.id,status="pending")
            db.add(mapping)
            await db.commit()
            monkeypatch.delenv("OPERLY_IDENTITY_HANDOFF_ENABLED",raising=False)
            assert await local_identity_allowed(db,a.id)
            monkeypatch.setenv("OPERLY_IDENTITY_HANDOFF_ENABLED","1")
            assert not await local_identity_allowed(db,a.id)
            assert await local_identity_allowed(db,b.id)
            assert not await local_identity_allowed(db,"missing")
            with pytest.raises(FederationDenied):
                await resolve_federated_user(db,verified_issuer=ISSUER,verified_subject=a.id)
            mapping.status="active"
            await db.commit()
            assert not await local_identity_allowed(db,a.id)
            assert (await resolve_federated_user(db,verified_issuer=ISSUER,verified_subject=a.id)).id==a.id
            assert await local_identity_allowed(db,b.id)
            mapping.status="disabled"
            await db.commit()
            assert not await local_identity_allowed(db,a.id)
            with pytest.raises(FederationDenied):
                await resolve_federated_user(db,verified_issuer=ISSUER,verified_subject=a.id)
            assert await local_identity_allowed(db,b.id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_federated_scope_rotation_preserves_authentication_age(monkeypatch):
    from datetime import datetime, timedelta
    from starlette.requests import Request
    from fastapi import HTTPException
    from packages.database.models import AuthSession, Tenant, TenantMember
    from apps.api.dependencies import AccountAuthContext
    from apps.api import session as routes
    monkeypatch.setenv("OPERLY_IDENTITY_HANDOFF_ENABLED", "1")
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "synthetic-test-pepper-" + "x" * 48)
    allowed = True
    checks = []
    async def status(db, *, user_id, authenticated_at):
        checks.append((user_id, authenticated_at))
        return allowed
    monkeypatch.setattr(routes, "federated_session_status", status)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[
                AppUser.__table__, Tenant.__table__, TenantMember.__table__,
                AuthSession.__table__, FederatedIdentityMap.__table__])
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            user = AppUser(id="synthetic-a", email="a@example.test", display_name="A", active=True)
            tenant = Tenant(id="synthetic-tenant", name="Synthetic", slug="synthetic")
            db.add_all([user, tenant])
            await db.flush()
            db.add_all([TenantMember(user_id=user.id, tenant_id=tenant.id, role="owner"),
                        FederatedIdentityMap(issuer=ISSUER, subject=user.id,
                            operly_user_id=user.id, status="active")])
            await db.flush()
            request = Request({"type":"http", "method":"POST", "path":"/", "headers":[]})
            current, _, _ = await routes._create_session(db, request, user.id, None, federated=True)
            original_auth = datetime.utcnow() - timedelta(hours=1)
            current.authenticated_at = original_auth
            original_expiry = current.expires_at
            await db.commit()
            assert not await local_identity_allowed(db, user.id)
            replacement, _, _ = await routes._rotate_authenticated_scope_session(
                db, request, AccountAuthContext(user, current), tenant.id)
            assert replacement.tenant_id == tenant.id
            assert replacement.authenticated_at == original_auth
            assert replacement.expires_at == original_expiry
            assert current.revoked_at is not None
            assert checks[-1] == (user.id, original_auth)
            await db.commit()
            allowed = False
            with pytest.raises(HTTPException) as exc:
                await routes._rotate_authenticated_scope_session(
                    db, request, AccountAuthContext(user, replacement), None)
            assert exc.value.status_code == 401
    finally:
        await engine.dispose()
