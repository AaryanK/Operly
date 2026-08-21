import json
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.principal_models import (
    ClientGrant,
    PrincipalConversation,
    PrincipalMessage,
    WorkspaceToolExposure,
)
from packages.database.schema import import_all_models
from packages.mcp.policy import active_client_scopes, effective_tool_access, exposed_tools
from packages.plugins.manifest import PluginManifest, PluginManifestRegistry, ToolManifest
from packages.security.principals import PrincipalService


class PrincipalAccessArchitectureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.user = AppUser(email="owner@example.test", display_name="Owner", active=True)
        self.tenant = Tenant(name="ANHITRA")
        self.db.add_all([self.user, self.tenant])
        await self.db.flush()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def test_guest_claim_moves_personal_history_without_creating_workspace_authority(self):
        guest = await PrincipalService.resolve_or_create_guest(
            self.db,
            provider="discord",
            provider_subject="123",
            display_name="Guest",
        )
        conversation = PrincipalConversation(
            principal_id=guest.id,
            provider="discord",
            external_conversation_id="dm-1",
            title="Guest conversation",
        )
        self.db.add(conversation)
        await self.db.flush()
        self.db.add(PrincipalMessage(conversation_id=conversation.id, role="user", content="remember this"))
        await self.db.flush()

        user_principal = await PrincipalService.claim_guest(
            self.db,
            guest_principal_id=guest.id,
            user_id=self.user.id,
            provider="discord",
            provider_subject="123",
        )
        await self.db.refresh(conversation)
        await self.db.refresh(guest)

        self.assertEqual(conversation.principal_id, user_principal.id)
        self.assertEqual(guest.status, "claimed")
        self.assertEqual(guest.claimed_by_user_id, self.user.id)
        self.assertIsNone(user_principal.expires_at)

    async def test_client_grant_and_workspace_tool_exposure_intersect(self):
        principal = await PrincipalService.user_principal(self.db, self.user.id)
        self.db.add(
            ClientGrant(
                principal_id=principal.id,
                tenant_id=self.tenant.id,
                client_id="chatgpt",
                scopes_json=json.dumps(["crm:read"]),
                status="active",
            )
        )
        self.db.add(
            WorkspaceToolExposure(
                tenant_id=self.tenant.id,
                tool_id="crm.search_leads",
                surface="mcp",
                exposed=True,
                access_mode="authenticated",
            )
        )
        await self.db.flush()
        scopes = await active_client_scopes(
            self.db,
            principal_id=principal.id,
            client_id="chatgpt",
            tenant_id=self.tenant.id,
        )
        tools = await exposed_tools(
            self.db,
            tenant_id=self.tenant.id,
            authenticated=True,
        )
        self.assertEqual(scopes, {"crm:read"})
        self.assertIn("crm.search_leads", tools)
        self.assertTrue(
            effective_tool_access(
                tool_id="crm.search_leads",
                principal_permissions={"crm:read"},
                required_permissions={"crm:read"},
                client_scopes=scopes,
                exposed=True,
            )
        )
        self.assertFalse(
            effective_tool_access(
                tool_id="crm.update_lead",
                principal_permissions={"crm:read", "crm:write"},
                required_permissions={"crm:write"},
                client_scopes=scopes,
                exposed=True,
            )
        )

    def test_plugin_registry_rejects_duplicate_operly_tool_ownership(self):
        registry = PluginManifestRegistry(
            [
                PluginManifest(
                    id="operly.crm",
                    version="1",
                    tools=(ToolManifest(id="crm.search", required_permissions=("crm:read",)),),
                )
            ]
        )
        with self.assertRaisesRegex(ValueError, "already provided"):
            registry.register(
                PluginManifest(
                    id="other.crm",
                    version="1",
                    tools=(ToolManifest(id="crm.search"),),
                )
            )


if __name__ == "__main__":
    unittest.main()
