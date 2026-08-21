import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext
from packages.database.db import Base
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models
from packages.database.workspace_security_models import WorkspaceRole, WorkspaceRolePermission
from packages.security.permissions import resolve_workspace_permissions
from packages.security.temporal_context import resolve_temporal_context, set_user_timezone


class UnifiedRuntimeContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.user = AppUser(email="owner@example.test", display_name="Owner", active=True)
        self.tenant = Tenant(name="ANHITRA", timezone="Asia/Kathmandu")
        self.other = Tenant(name="NaySchool", timezone="Asia/Kathmandu")
        self.db.add_all([self.user, self.tenant, self.other])
        await self.db.flush()
        self.db.add_all(
            [
                TenantMember(tenant_id=self.tenant.id, user_id=self.user.id, role="owner"),
                TenantMember(tenant_id=self.other.id, user_id=self.user.id, role="owner"),
            ]
        )
        await self.db.flush()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def test_actor_timezone_overrides_workspace_timezone_everywhere(self):
        await set_user_timezone(self.db, user_id=self.user.id, timezone_name="America/Chicago")
        temporal = await resolve_temporal_context(
            self.db, user_id=self.user.id, tenant_id=self.tenant.id
        )
        self.assertEqual(temporal.actor_timezone, "America/Chicago")
        self.assertEqual(temporal.workspace_timezone, "Asia/Kathmandu")
        self.assertNotEqual(temporal.actor_now.utcoffset(), temporal.workspace_now.utcoffset())
        self.assertEqual(temporal.as_dict()["relative_time_default"], "actor")

    async def test_system_role_inherits_new_default_permissions(self):
        role = WorkspaceRole(
            tenant_id=self.tenant.id,
            key="owner",
            name="Owner",
            is_system=True,
        )
        self.db.add(role)
        await self.db.flush()
        self.db.add(WorkspaceRolePermission(role_id=role.id, permission="crm:read"))
        await self.db.flush()
        permissions = await resolve_workspace_permissions(
            self.db, tenant_id=self.tenant.id, role="owner"
        )
        self.assertIn("discord:write", permissions)
        self.assertIn("workspace:read", permissions)

    async def test_personal_workspace_tools_exist_in_dm_but_not_shared_discord_server(self):
        harness = PluginAgentHarness()
        authority = {"workspace:read", "discord:read", "discord:write"}
        dm = PluginInvocationContext(
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            role="owner",
            objective="what workspaces do I belong to?",
            channel="discord",
            metadata={"is_direct": True},
        )
        server = PluginInvocationContext(
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            role="owner",
            objective="what workspaces do I belong to?",
            channel="discord",
            metadata={"is_direct": False},
        )
        self.assertTrue(harness.capability_authorized("account.list_workspaces", authority, dm))
        self.assertFalse(harness.capability_authorized("account.list_workspaces", authority, server))
        self.assertTrue(harness.capability_authorized("discord.send_dm", authority, server))
        self.assertFalse(harness.capability_authorized("discord.send_message", authority, PluginInvocationContext(
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            role="owner",
            objective="send this",
            channel="web",
            metadata={},
        )))


if __name__ == "__main__":
    unittest.main()
