"""Native identity projection never grants workspace permissions or claims an email."""
import os
from uuid import uuid4
from datetime import datetime, timezone
import httpx
import pytest
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from packages.database.db import Base
from packages.database.models import AppUser,Tenant,TenantMember,AuthIdentity
from packages.database.federated_identity_models import FederatedIdentityMap
from packages.security.native_identity_projection import resolve_or_provision_native
from packages.security.federated_identity import FederationDenied
from packages.security.identity_handoff import local_identity_allowed
from packages.security.federated_session_status import ISSUER

@pytest.mark.asyncio
async def test_verified_native_projection_is_exact_and_permissionless(monkeypatch):
    monkeypatch.setenv("OPERLY_IDENTITY_HANDOFF_ENABLED","1")
    monkeypatch.setenv("OPERLY_NATIVE_PROJECTION_ENABLED","1")
    monkeypatch.setenv("OPERLY_FEDERATED_STATUS_ENABLED","1")
    monkeypatch.setenv("OPERLY_FEDERATED_STATUS_SECRET","synthetic-only-"+"x"*43)
    new,collision,disabled=[str(uuid4()) for _ in range(3)]
    class Client:
        def __init__(self,**kwargs):
            assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs):
            assert url==ISSUER+"/identity/v1/relying-party/native-projection"
            sid=kwargs["json"]["subject"]
            return httpx.Response(200,json={"subject":sid,
                "email":"existing@example.test" if sid==collision else "native@example.test",
                "display_name":"Synthetic native",
                "email_verified_at":"2026-01-01T00:00:00+00:00"})
    monkeypatch.setattr(httpx,"AsyncClient",Client)
    engine=create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all,tables=[AppUser.__table__,
                Tenant.__table__,TenantMember.__table__,AuthIdentity.__table__,FederatedIdentityMap.__table__])
        async with async_sessionmaker(engine,expire_on_commit=False)() as db:
            db.add(AppUser(id=disabled,email="existing@example.test",active=True))
            db.add(FederatedIdentityMap(issuer=ISSUER,subject=disabled,operly_user_id=disabled,status="disabled"))
            await db.commit()
            async def provision(sid,issuer=ISSUER):
                return await resolve_or_provision_native(db,verified_issuer=issuer,verified_subject=sid)
            with pytest.raises(FederationDenied): await provision(collision)
            with pytest.raises(FederationDenied): await provision(disabled)
            with pytest.raises(FederationDenied): await provision(new,"https://other.example.test")
            person=await provision(new)
            await db.commit()
            assert person.id==new and person.password_hash is None
            assert (await provision(new)).id==new
            assert not await local_identity_allowed(db,new)
            assert await db.scalar(select(func.count()).select_from(TenantMember))==0
            assert await db.scalar(select(func.count()).select_from(AuthIdentity))==0
            assert await db.scalar(select(func.count()).select_from(Tenant))==0
            assert await db.scalar(select(func.count()).select_from(AppUser))==2
    finally:
        await engine.dispose()
