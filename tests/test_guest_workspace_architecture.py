import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.channels.envelope import ChannelEnvelope
from packages.channels.service import ChannelService
from packages.database.channel_models import ChannelInstallation
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.schema import import_all_models
from packages.security.execution_context import ExecutionContextError, resolve_execution_context
from packages.security.guest_workspace import set_guest_workspace_policy


class GuestWorkspaceArchitectureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _guest_workspace(self):
        async with self.sessions() as db:
            tenant = Tenant(name="Research Discord")
            db.add(tenant)
            await db.flush()
            installation = ChannelInstallation(
                tenant_id=tenant.id,
                provider="discord",
                external_space_id="12345",
                display_name="Research Discord",
                provisional=True,
                status="connected",
                metadata_json="{}",
            )
            db.add(installation)
            await db.commit()
            return tenant.id, installation.id

    async def test_unlinked_external_principal_gets_only_operly_guest_baseline(self):
        tenant_id, _ = await self._guest_workspace()
        async with self.sessions() as db:
            execution = await resolve_execution_context(
                db,
                workspace_id=tenant_id,
                user_id=None,
                channel="discord",
                metadata={
                    "external_space_id": "12345",
                    "principal_id": "guest:raju",
                    "_guest_principal_id": "guest:raju",
                },
                require_membership=True,
            )

        self.assertTrue(execution.is_guest_workspace)
        self.assertEqual(execution.principal_id, "guest:raju")
        self.assertIn("model:invoke", execution.permissions)
        self.assertIn("tasks:write", execution.permissions)
        self.assertNotIn("discord:read", execution.permissions)
        self.assertNotIn("discord:write", execution.permissions)
        self.assertNotIn("gmail:read", execution.permissions)
        self.assertNotIn("crm:read", execution.permissions)
        self.assertNotIn("files:process", execution.permissions)
        self.assertNotIn("context:tenant:read", execution.permissions)

    async def test_linked_nonmember_stays_guest_in_provisional_workspace(self):
        tenant_id, _ = await self._guest_workspace()
        async with self.sessions() as db:
            user = AppUser(email="raju@example.test", display_name="Raju", active=True)
            db.add(user)
            await db.commit()
            user_id = user.id

        async with self.sessions() as db:
            execution = await resolve_execution_context(
                db,
                workspace_id=tenant_id,
                user_id=user_id,
                channel="discord",
                metadata={"external_space_id": "12345", "principal_id": f"user:{user_id}"},
                require_membership=True,
            )

        self.assertTrue(execution.is_guest_workspace)
        self.assertFalse(execution.is_member)
        self.assertEqual(execution.principal_id, f"user:{user_id}")
        self.assertNotIn("gmail:read", execution.permissions)

    async def test_admin_policy_can_remove_file_access_even_when_platform_allows_it(self):
        tenant_id, installation_id = await self._guest_workspace()
        async with self.sessions() as db:
            await set_guest_workspace_policy(
                db,
                installation_id=installation_id,
                deny=["files:process"],
            )
            await db.commit()

        async with self.sessions() as db:
            execution = await resolve_execution_context(
                db,
                workspace_id=tenant_id,
                user_id=None,
                channel="discord",
                metadata={
                    "external_space_id": "12345",
                    "_guest_principal_id": "guest:raju",
                    "_operly_platform_permissions": [
                        "discord:read",
                        "discord:write",
                        "files:process",
                        "gmail:read",
                    ],
                },
                require_membership=True,
            )

        self.assertIn("discord:read", execution.permissions)
        self.assertNotIn("files:process", execution.permissions)
        self.assertNotIn("gmail:read", execution.permissions)

    async def test_source_admin_gets_guest_management_not_full_operly_authority(self):
        tenant_id, _ = await self._guest_workspace()
        async with self.sessions() as db:
            execution = await resolve_execution_context(
                db,
                workspace_id=tenant_id,
                user_id=None,
                channel="discord",
                metadata={
                    "external_space_id": "12345",
                    "_guest_principal_id": "guest:admin",
                    "_operly_platform_admin": True,
                },
                require_membership=True,
            )

        self.assertEqual(execution.role, "guest_admin")
        self.assertIn("workspace:settings:manage", execution.permissions)
        self.assertIn("actions:read", execution.permissions)
        self.assertNotIn("crm:write", execution.permissions)
        self.assertNotIn("gmail:write", execution.permissions)

    async def test_claimed_workspace_never_falls_back_to_guest_authority(self):
        tenant_id, installation_id = await self._guest_workspace()
        async with self.sessions() as db:
            installation = await db.get(ChannelInstallation, installation_id)
            installation.provisional = False
            await db.commit()

        async with self.sessions() as db:
            with self.assertRaises(ExecutionContextError):
                await resolve_execution_context(
                    db,
                    workspace_id=tenant_id,
                    user_id=None,
                    channel="discord",
                    metadata={
                        "external_space_id": "12345",
                        "_guest_principal_id": "guest:raju",
                    },
                    require_membership=True,
                )

    async def test_channel_resolution_auto_creates_guest_workspace_and_principal(self):
        envelope = ChannelEnvelope(
            provider="discord",
            external_user_id="222",
            external_space_id="333",
            external_conversation_id="444",
            actor_name="Raju",
            text="What did we decide about the detector geometry?",
            space_name="Research Group",
            is_direct=False,
        )
        async with self.sessions() as db:
            resolved = await ChannelService.resolve(db, envelope)
            await db.commit()

        self.assertTrue(resolved.is_guest_workspace)
        self.assertEqual(resolved.role, "guest")
        self.assertIsNone(resolved.user_id)
        self.assertTrue(str(resolved.principal_id).startswith("guest:"))
        self.assertTrue(resolved.allow_tenant_context)

    async def test_guest_attachment_ingress_fails_closed_without_platform_file_permission(self):
        envelope = ChannelEnvelope(
            provider="discord",
            external_user_id="555",
            external_space_id="666",
            external_conversation_id="777",
            actor_name="Raju",
            text="read this",
            space_name="Research Group",
            is_direct=False,
            metadata={"has_attachments": True},
        )
        async with self.sessions() as db:
            resolved = await ChannelService.resolve(db, envelope)
            await db.commit()

        self.assertTrue(resolved.is_guest_workspace)
        self.assertFalse(resolved.allow_tenant_context)
        self.assertFalse(resolved.can_process_files)

    async def test_envelope_cannot_self_assert_guest_platform_file_permission(self):
        envelope = ChannelEnvelope(
            provider="discord",
            external_user_id="888",
            external_space_id="999",
            external_conversation_id="1000",
            actor_name="Untrusted User",
            text="read this",
            space_name="Research Group",
            is_direct=False,
            metadata={
                "has_attachments": True,
                "_operly_platform_permissions": [
                    "discord:read",
                    "discord:write",
                    "files:process",
                ],
                "_operly_platform_admin": True,
            },
        )
        async with self.sessions() as db:
            resolved = await ChannelService.resolve(db, envelope)
            await db.commit()

        self.assertTrue(resolved.is_guest_workspace)
        self.assertFalse(resolved.allow_tenant_context)
        self.assertFalse(resolved.can_process_files)
        self.assertFalse(resolved.platform_admin)


if __name__ == "__main__":
    unittest.main()
