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
