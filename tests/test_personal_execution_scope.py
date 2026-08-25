import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.business_brain.personal_agent import PERSONAL_SYSTEM_PROMPT, PersonalAgentService
from packages.capabilities.personal_provider import PersonalRuntimeProvider
from packages.database.db import Base
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models
from packages.security.execution_context import (
    ExecutionContextError,
    ScopeKind,
    resolve_personal_execution_context,
)
from packages.security.surfaces import SurfaceKind


class PersonalExecutionScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with self.sessions() as db:
            self.user = AppUser(
                email="personal-scope@example.test",
                display_name="Personal Owner",
                active=True,
            )
            self.workspace = Tenant(name="ANHITRA", slug="anhitra")
            self.other_workspace = Tenant(name="Other", slug="other")
            db.add_all([self.user, self.workspace, self.other_workspace])
            await db.flush()
            db.add(
                TenantMember(
                    tenant_id=self.workspace.id,
                    user_id=self.user.id,
                    role="employee",
                )
            )
            await db.commit()
            self.user_id = self.user.id
            self.workspace_id = self.workspace.id
            self.other_workspace_id = self.other_workspace.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_personal_context_has_no_workspace_authority(self):
        async with self.sessions() as db:
            context = await resolve_personal_execution_context(
                db,
                user_id=self.user_id,
                channel="web",
                surface=SurfaceKind.PERSONAL_PRIVATE,
            )

        self.assertEqual(context.scope_kind, ScopeKind.PERSONAL)
        self.assertTrue(context.is_personal)
        self.assertFalse(context.is_workspace)
        self.assertEqual(context.scope_id, f"personal:{self.user_id}")
        self.assertIsNone(context.workspace_id)
        self.assertIsNone(context.focus_workspace_id)
        self.assertIsNone(context.membership_id)
        self.assertEqual(context.role, "personal_owner")
        self.assertTrue(context.can("tasks:write"))
        self.assertFalse(context.can("workspace:roles:manage"))

    async def test_workspace_focus_is_validated_but_never_becomes_authority(self):
        async with self.sessions() as db:
            context = await resolve_personal_execution_context(
                db,
                user_id=self.user_id,
                channel="discord",
                surface=SurfaceKind.DISCORD_DM,
                focus_workspace_id=self.workspace_id,
            )

        self.assertEqual(context.scope_kind, ScopeKind.PERSONAL)
        self.assertIsNone(context.workspace_id)
        self.assertEqual(context.focus_workspace_id, self.workspace_id)
        # The user is only an employee in ANHITRA. Personal scope must not inherit
        # ANHITRA membership, role, or workspace permissions from the focus hint.
        self.assertIsNone(context.membership_id)
        self.assertEqual(context.role, "personal_owner")
        self.assertFalse(context.can("crm:write"))

    async def test_unowned_workspace_cannot_be_used_even_as_focus(self):
        async with self.sessions() as db:
            with self.assertRaisesRegex(ExecutionContextError, "focus is unavailable"):
                await resolve_personal_execution_context(
                    db,
                    user_id=self.user_id,
                    channel="web",
                    surface=SurfaceKind.PERSONAL_PRIVATE,
                    focus_workspace_id=self.other_workspace_id,
                )

    async def _assert_personal_software_delegation(self, capability_id, arguments):
        provider = PersonalRuntimeProvider()
        definition = SimpleNamespace(id=capability_id)
        registry = SimpleNamespace(
            definition=lambda requested: definition if requested == capability_id else None,
            describe=lambda *args, **kwargs: [
                {
                    "id": capability_id,
                    "authorized": True,
                    "availability": {"available": True},
                }
            ],
        )
        exposed = []
        view = SimpleNamespace(expose=lambda values: exposed.extend(values))
        captured = {}

        async def invoke(_self, requested_id, supplied, plugin_context):
            captured["id"] = requested_id
            captured["arguments"] = supplied
            captured["context"] = plugin_context
            return {
                "ok": True,
                "changed": True,
                "external_reference": "canonical-project-id",
                "evidence": {"workspace_id": plugin_context.tenant_id},
            }

        context_metadata = {
            "personal_scope": True,
            "is_direct": True,
            "objective": f"delegate {capability_id}",
        }
        async with self.sessions() as db:
            context = SimpleNamespace(
                actor_id=self.user_id,
                db=db,
                tenant_id=None,
                invocation={"channel": "web", "metadata": context_metadata},
            )
            with (
                patch(
                    "packages.capabilities.personal_provider.resolve_workspace_permissions",
                    new=AsyncMock(return_value=frozenset({"workspace:read", "solution:read", "solution:generate"})),
                ),
                patch(
                    "packages.capabilities.agent_harness.PluginAgentHarness.registry_for",
                    new=AsyncMock(return_value=registry),
                ),
                patch(
                    "packages.capabilities.agent_harness.PluginAgentHarness.session_view_for",
                    new=AsyncMock(return_value=view),
                ),
                patch(
                    "packages.capabilities.agent_harness.PluginAgentHarness.capability_authorized",
                    return_value=True,
                ),
                patch(
                    "packages.capabilities.agent_harness.PluginAgentHarness.invoke",
                    new=invoke,
                ),
            ):
                result = await provider.execute(
                    context,
                    "account.workspace_execute",
                    {
                        "workspace": self.workspace_id,
                        "capability_id": capability_id,
                        "arguments": arguments,
                    },
                )

        self.assertTrue(result.success)
        self.assertTrue(result.changed)
        self.assertEqual(result.evidence["workspace_id"], self.workspace_id)
        self.assertEqual(result.evidence["capability_id"], capability_id)
        self.assertEqual(exposed, [capability_id])
        self.assertEqual(captured["id"], capability_id)
        self.assertEqual(captured["arguments"], arguments)
        delegated = captured["context"]
        self.assertEqual(delegated.tenant_id, self.workspace_id)
        self.assertEqual(delegated.user_id, self.user_id)
        self.assertEqual(delegated.role, "employee")
        self.assertTrue(delegated.metadata["personal_delegate"])
        self.assertTrue(delegated.metadata["is_direct"])
        self.assertFalse(delegated.metadata["shared_surface"])

    async def test_personal_software_build_delegates_into_live_workspace_harness(self):
        await self._assert_personal_software_delegation(
            "software.build",
            {
                "project_id": "existing-canonical-project-id",
                "objective": "Build the existing project in place",
                "return_source_archive": True,
            },
        )

    async def test_personal_software_edit_delegates_into_live_workspace_harness(self):
        await self._assert_personal_software_delegation(
            "software.edit",
            {
                "project_id": "existing-canonical-project-id",
                "instruction": "Change the hero copy",
                "studio_context": {"route": "/"},
            },
        )

    async def test_personal_software_delegation_rejects_unowned_workspace_before_harness(self):
        provider = PersonalRuntimeProvider()
        async with self.sessions() as db:
            context = SimpleNamespace(
                actor_id=self.user_id,
                db=db,
                tenant_id=None,
                invocation={"channel": "web", "metadata": {"personal_scope": True}},
            )
            with patch(
                "packages.capabilities.agent_harness.PluginAgentHarness.invoke",
                new=AsyncMock(),
            ) as invoke:
                result = await provider.execute(
                    context,
                    "account.workspace_execute",
                    {
                        "workspace": self.other_workspace_id,
                        "capability_id": "software.build",
                        "arguments": {"objective": "should never execute"},
                    },
                )
                invoke.assert_not_awaited()

        self.assertFalse(result.success)
        self.assertEqual(result.evidence["reason"], "workspace_ambiguous_or_missing")

    def test_personal_agent_does_not_promote_selected_workspace_to_scope(self):
        source = inspect.getsource(PersonalAgentService.run)
        self.assertIn('personal_scope_id = f"personal:{user_id}"', source)
        self.assertNotIn("personal_scope_id = selected_workspace_id or", source)
        self.assertIn('"focus_workspace_id": selected_workspace_id', source)
        self.assertIn('"tenant_id": None', source)
        self.assertIn("selected/focused workspace is only a disambiguation hint", PERSONAL_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
