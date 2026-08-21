import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.channels.space_bindings import ExternalSpaceBindingService, SpaceBindingError
from packages.database.channel_models import ChannelInstallation
from packages.database.db import Base
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models


class SpaceBindingTransferTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "OPERLY_ENV": "test",
                "SESSION_SECRET": "binding-test-session-secret-that-is-long-enough",
                "AUTH_TOKEN_PEPPER": "binding-test-token-pepper-that-is-long-enough",
            },
        )
        self.environment.start()
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "binding.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        import_all_models()
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tmp.cleanup()
        self.environment.stop()

    async def test_owner_can_explicitly_rebind_space_between_owned_workspaces(self):
        async with self.sessions() as db:
            user = AppUser(email="owner@example.com", display_name="Owner", active=True)
            old = Tenant(name="Old Workspace")
            new = Tenant(name="New Workspace")
            db.add_all([user, old, new])
            await db.flush()
            db.add_all([
                TenantMember(tenant_id=old.id, user_id=user.id, role="owner"),
                TenantMember(tenant_id=new.id, user_id=user.id, role="owner"),
            ])
            binding = ChannelInstallation(
                tenant_id=old.id,
                provider="discord",
                external_space_id="12345",
                display_name="Test Server",
                provisional=False,
                status="connected",
                metadata_json="{}",
            )
            db.add(binding)
            await db.flush()

            moved = await ExternalSpaceBindingService.bind(
                db,
                provider="discord",
                external_space_id="12345",
                display_name="Test Server",
                user_id=user.id,
                tenant_id=new.id,
                external_authority_verified=True,
            )
            self.assertEqual(moved.tenant_id, new.id)
            self.assertFalse(moved.provisional)
            self.assertEqual(moved.status, "connected")

    async def test_rebind_rejected_when_user_cannot_manage_source_workspace(self):
        async with self.sessions() as db:
            user = AppUser(email="owner@example.com", display_name="Owner", active=True)
            old = Tenant(name="Someone Else")
            new = Tenant(name="My Workspace")
            db.add_all([user, old, new])
            await db.flush()
            db.add(TenantMember(tenant_id=new.id, user_id=user.id, role="owner"))
            db.add(ChannelInstallation(
                tenant_id=old.id,
                provider="discord",
                external_space_id="12345",
                display_name="Test Server",
                provisional=False,
                status="connected",
                metadata_json="{}",
            ))
            await db.flush()

            with self.assertRaises(SpaceBindingError):
                await ExternalSpaceBindingService.bind(
                    db,
                    provider="discord",
                    external_space_id="12345",
                    display_name="Test Server",
                    user_id=user.id,
                    tenant_id=new.id,
                    external_authority_verified=True,
                )


if __name__ == "__main__":
    unittest.main()
