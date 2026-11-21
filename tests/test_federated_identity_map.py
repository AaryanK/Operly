"""Federation gate rejects unactivated mappings and foreign identities."""
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from packages.database.db import Base
from packages.database.models import AppUser
from packages.database.federated_identity_models import FederatedIdentityMap
from packages.security.federated_identity import FederationDenied, resolve_federated_user


@pytest.mark.asyncio
async def test_federation_does_not_grant_pending_or_disabled_user_access():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[
            AppUser.__table__, FederatedIdentityMap.__table__
        ])
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        person = AppUser(id="existing-operly-user", email="user@example.test",
                         display_name="Existing User", active=True)
        db.add(person)
        db.add(FederatedIdentityMap(
            issuer="https://identity.dragonzpyder.xyz",
            subject="immutable-subject", operly_user_id=person.id, status="pending",
        ))
        await db.commit()
        with pytest.raises(FederationDenied):
            await resolve_federated_user(
                db, verified_issuer="https://identity.dragonzpyder.xyz",
                verified_subject="immutable-subject",
            )
        mapping = await db.get(FederatedIdentityMap, ("https://identity.dragonzpyder.xyz", "immutable-subject"))
        mapping.status = "active"
        await db.commit()
        assert (await resolve_federated_user(
            db, verified_issuer="https://identity.dragonzpyder.xyz",
            verified_subject="immutable-subject",
        )).id == person.id
        with pytest.raises(FederationDenied):
            await resolve_federated_user(
                db, verified_issuer="https://other.example.test",
                verified_subject="immutable-subject",
            )
        person.active = False
        await db.commit()
        with pytest.raises(FederationDenied):
            await resolve_federated_user(
                db, verified_issuer="https://identity.dragonzpyder.xyz",
                verified_subject="immutable-subject",
            )
    await engine.dispose()
