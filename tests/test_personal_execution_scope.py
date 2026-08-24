import inspect
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.business_brain.personal_agent import PERSONAL_SYSTEM_PROMPT, PersonalAgentService
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

    def test_personal_agent_does_not_promote_selected_workspace_to_scope(self):
        source = inspect.getsource(PersonalAgentService.run)
        self.assertIn('personal_scope_id = f"personal:{user_id}"', source)
        self.assertNotIn("personal_scope_id = selected_workspace_id or", source)
        self.assertIn('"focus_workspace_id": selected_workspace_id', source)
        self.assertIn('"tenant_id": None', source)
        self.assertIn("selected/focused workspace is only a disambiguation hint", PERSONAL_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
