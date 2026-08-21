import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.channels.envelope import ChannelEnvelope
from packages.channels.identity import IdentityService
from packages.channels.service import ChannelService
from packages.database.db import Base
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models


class DirectMessageWorkspaceSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "OPERLY_ENV": "test",
                "SESSION_SECRET": "dm-switch-session-secret-that-is-long-enough",
                "AUTH_TOKEN_PEPPER": "dm-switch-token-pepper-that-is-long-enough",
            },
        )
        self.environment.start()
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "dm-switch.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        import_all_models()
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tmp.cleanup()
        self.environment.stop()

    async def test_explicit_workspace_switch_clears_old_agent_conversation(self):
        async with self.sessions() as db:
            user = AppUser(email="aaryan@example.com", display_name="Aaryan", active=True)
            first = Tenant(name="Operly Labs", slug="operly")
            second = Tenant(name="Nayschool", slug="nayschool")
            db.add_all([user, first, second])
            await db.flush()
            db.add_all(
                [
                    TenantMember(tenant_id=first.id, user_id=user.id, role="owner"),
                    TenantMember(tenant_id=second.id, user_id=user.id, role="owner"),
                ]
            )
            await IdentityService.link_external_identity(
                db,
                user_id=user.id,
                provider="discord",
                external_user_id="99887766",
                display_name="Aaryan",
            )
            await IdentityService.upsert_conversation_state(
                db,
                provider="discord",
                external_user_id="99887766",
                external_conversation_id="dm-a",
                user_id=user.id,
                active_tenant_id=first.id,
                agent_conversation_id="conversation-from-operly",
                metadata={"direct": True},
            )
            await db.commit()

            resolved = await ChannelService.resolve(
                db,
                ChannelEnvelope(
                    provider="discord",
                    external_user_id="99887766",
                    external_conversation_id="dm-a",
                    actor_name="Aaryan",
                    text="In Nayschool add a contact",
                    is_direct=True,
                ),
            )
            state = await IdentityService.conversation_state(
                db,
                provider="discord",
                external_user_id="99887766",
                external_conversation_id="dm-a",
            )

        self.assertEqual(resolved.tenant_id, second.id)
        self.assertIsNotNone(state)
        self.assertEqual(state.active_tenant_id, second.id)
        self.assertIsNone(state.agent_conversation_id)
