import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.capabilities.agent_harness import PluginAgentHarness
from packages.channels.envelope import ChannelEnvelope
from packages.channels.identity import IdentityService
from packages.channels.linking import IdentityLinkService
from packages.channels.service import ChannelService
from packages.context.service import ContextService
from packages.database.channel_models import ChannelInstallation
from packages.database.db import Base
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models


class ChannelContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "OPERLY_ENV": "test",
                "SESSION_SECRET": "channel-test-session-secret-that-is-long-enough",
                "AUTH_TOKEN_PEPPER": "channel-test-token-pepper-that-is-long-enough",
            },
        )
        self.environment.start()
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "channel.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        import_all_models()
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tmp.cleanup()
        self.environment.stop()

    async def seed_team(self):
        async with self.sessions() as db:
            aaryan = AppUser(email="aaryan@example.com", display_name="Aaryan", active=True)
            bob = AppUser(email="bob@example.com", display_name="Bob", active=True)
            operly = Tenant(name="Operly Labs", slug="operly")
            coffee = Tenant(name="Dragon Coffee", slug="coffee")
            db.add_all([aaryan, bob, operly, coffee])
            await db.flush()
            db.add_all(
                [
                    TenantMember(tenant_id=operly.id, user_id=aaryan.id, role="owner"),
                    TenantMember(tenant_id=operly.id, user_id=bob.id, role="employee"),
                    TenantMember(tenant_id=coffee.id, user_id=aaryan.id, role="manager"),
                ]
            )
            await db.commit()
            return aaryan.id, bob.id, operly.id, coffee.id

    async def test_private_human_and_conversation_context_never_leaks_to_peer(self):
        aaryan_id, bob_id, tenant_id, _ = await self.seed_team()
        async with self.sessions() as db:
            await ContextService.remember_human(
                db,
                user_id=aaryan_id,
                tenant_id=tenant_id,
                content="I may replace our current accountant.",
            )
            await ContextService.remember_tenant(
                db,
                tenant_id=tenant_id,
                content="Acme wants the proposal by Friday.",
            )
            await ContextService.remember_conversation(
                db,
                tenant_id=tenant_id,
                conversation_id="dm-1",
                user_id=aaryan_id,
                private=True,
                content="Private salary planning note.",
            )
            await ContextService.remember_conversation(
                db,
                tenant_id=tenant_id,
                conversation_id="dm-1",
                user_id=None,
                private=False,
                content="Shared project decision.",
            )
            await db.commit()

            owner = await ContextService.load_for_agent(
                db,
                tenant_id=tenant_id,
                user_id=aaryan_id,
                conversation_id="dm-1",
                allow_tenant_context=True,
                query="proposal",
            )
            peer = await ContextService.load_for_agent(
                db,
                tenant_id=tenant_id,
                user_id=bob_id,
                conversation_id="dm-1",
                allow_tenant_context=True,
                query="planning",
            )

        owner_prompt = owner.as_prompt()
        peer_prompt = peer.as_prompt()
        self.assertIn("Acme wants the proposal by Friday", owner_prompt)
        self.assertNotIn("replace our current accountant", owner_prompt)
        self.assertNotIn("Private salary planning note", owner_prompt)
        self.assertNotIn("Shared project decision", owner_prompt)
        self.assertNotIn("replace our current accountant", peer_prompt)
        self.assertNotIn("Private salary planning note", peer_prompt)
        self.assertNotIn("Acme wants the proposal by Friday", peer_prompt)
        self.assertNotIn("Shared project decision", peer_prompt)

    async def test_same_discord_human_resolves_different_server_memberships_and_dm_state(self):
        aaryan_id, _, operly_id, coffee_id = await self.seed_team()
        async with self.sessions() as db:
            await IdentityService.link_external_identity(
                db,
                user_id=aaryan_id,
                provider="discord",
                external_user_id="99887766",
                display_name="Aaryan",
            )
            db.add_all(
                [
                    ChannelInstallation(
                        tenant_id=operly_id,
                        provider="discord",
                        external_space_id="111",
                        display_name="Operly Discord",
                        provisional=False,
                    ),
                    ChannelInstallation(
                        tenant_id=coffee_id,
                        provider="discord",
                        external_space_id="222",
                        display_name="Coffee Discord",
                        provisional=False,
                    ),
                ]
            )
            await db.commit()

            first = await ChannelService.resolve(
                db,
                ChannelEnvelope(
                    provider="discord",
                    external_user_id="99887766",
                    external_space_id="111",
                    external_conversation_id="chan-a",
                    actor_name="Aaryan",
                    text="hello",
                ),
            )
            second = await ChannelService.resolve(
                db,
                ChannelEnvelope(
                    provider="discord",
                    external_user_id="99887766",
                    external_space_id="222",
                    external_conversation_id="chan-b",
                    actor_name="Aaryan",
                    text="hello",
                ),
            )
            personal_dm = await ChannelService.resolve(
                db,
                ChannelEnvelope(
                    provider="discord",
                    external_user_id="99887766",
                    external_conversation_id="dm-a",
                    actor_name="Aaryan",
                    text="What changed?",
                    is_direct=True,
                ),
            )
            selected_dm = await ChannelService.resolve(
                db,
                ChannelEnvelope(
                    provider="discord",
                    external_user_id="99887766",
                    external_conversation_id="dm-a",
                    actor_name="Aaryan",
                    text="I mean Dragon Coffee",
                    is_direct=True,
                ),
            )
            continued_dm = await ChannelService.resolve(
                db,
                ChannelEnvelope(
                    provider="discord",
                    external_user_id="99887766",
                    external_conversation_id="dm-a",
                    actor_name="Aaryan",
                    text="What should I do next?",
                    is_direct=True,
                ),
            )

        self.assertEqual((first.tenant_id, first.role), (operly_id, "owner"))
        self.assertEqual((second.tenant_id, second.role), (coffee_id, "manager"))
        self.assertIsNone(personal_dm.tenant_id)
        self.assertFalse(personal_dm.allow_tenant_context)
        self.assertEqual(personal_dm.user_id, aaryan_id)
        self.assertEqual({item["id"] for item in personal_dm.options}, {operly_id, coffee_id})
        self.assertEqual({item["role"] for item in personal_dm.options}, {"owner", "manager"})
        self.assertEqual(selected_dm.tenant_id, coffee_id)
        self.assertFalse(selected_dm.allow_tenant_context)
        self.assertEqual(continued_dm.tenant_id, coffee_id)
        self.assertFalse(continued_dm.allow_tenant_context)

    async def test_new_discord_space_is_provisional_guest_with_narrow_operly_baseline(self):
        async with self.sessions() as db:
            installation = await IdentityService.ensure_installation(
                db,
                provider="discord",
                external_space_id="333",
                display_name="New Server",
            )
            resolution = await ChannelService.resolve(
                db,
                ChannelEnvelope(
                    provider="discord",
                    external_user_id="anonymous",
                    external_space_id="333",
                    external_conversation_id="general",
                    actor_name="Unknown",
                    text="delete everything",
                ),
            )
        self.assertTrue(installation.provisional)
        self.assertEqual(resolution.tenant_id, installation.tenant_id)
        self.assertTrue(resolution.is_guest_workspace)
        self.assertEqual(resolution.role, "guest")
        self.assertIn("model:invoke", resolution.effective_permissions)
        self.assertIn("context:conversation:read", resolution.effective_permissions)
        self.assertNotIn("gmail:read", resolution.effective_permissions)
        self.assertNotIn("context:tenant:read", resolution.effective_permissions)
        self.assertEqual(PluginAgentHarness().authority("guest"), set())
        self.assertEqual(PluginAgentHarness().authority("unknown-role"), set())

    async def test_bidirectional_pairing_is_single_use(self):
        aaryan_id, bob_id, _, _ = await self.seed_team()
        async with self.sessions() as db:
            from_operly = await IdentityLinkService.create_from_operly(
                db,
                user_id=aaryan_id,
                provider="discord",
            )
            linked = await IdentityLinkService.claim_from_channel(
                db,
                provider="discord",
                external_user_id="12345",
                code=from_operly.code,
                display_name="Aaryan Discord",
            )
            self.assertEqual(linked.user_id, aaryan_id)
            with self.assertRaises(ValueError):
                await IdentityLinkService.claim_from_channel(
                    db,
                    provider="discord",
                    external_user_id="99999",
                    code=from_operly.code,
                )

            from_channel = await IdentityLinkService.create_from_channel(
                db,
                provider="discord",
                external_user_id="67890",
                display_name="Bob Discord",
            )
            info = await IdentityLinkService.inspect_channel_token(
                db,
                provider="discord",
                token=from_channel.token,
            )
            self.assertEqual(info["display_name"], "Bob Discord")
            linked_bob = await IdentityLinkService.claim_from_web(
                db,
                user_id=bob_id,
                provider="discord",
                token=from_channel.token,
            )
            self.assertEqual(linked_bob.user_id, bob_id)
            with self.assertRaises(ValueError):
                await IdentityLinkService.claim_from_web(
                    db,
                    user_id=aaryan_id,
                    provider="discord",
                    token=from_channel.token,
                )
