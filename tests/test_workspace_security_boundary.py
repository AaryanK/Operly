import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.dependencies import AuthContext
from apps.api.schemas import (
    WorkspaceCreateInput,
    WorkspaceMemberAddInput,
    WorkspaceMemberRoleInput,
    WorkspaceRoleCreateInput,
)
from apps.api.workspace_router import (
    add_workspace_member,
    create_workspace,
    create_workspace_role,
    set_workspace_member_role,
)
from packages.business_brain.context_loader import load_business_context
from packages.channels.envelope import ChannelEnvelope
from packages.channels.service import ChannelService
from packages.context.service import ContextService
from packages.database.channel_models import ChannelInstallation
from packages.database.db import Base
from packages.database.models import AppUser, Memory, Message, Task, Tenant, TenantMember
from packages.database.schema import import_all_models
from packages.security.execution_context import resolve_execution_context
from packages.security.permissions import resolve_workspace_permissions


class WorkspaceSecurityBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "OPERLY_ENV": "test",
                "SESSION_SECRET": "workspace-test-session-secret-that-is-long-enough",
                "AUTH_TOKEN_PEPPER": "workspace-test-token-pepper-that-is-long-enough",
            },
        )
        self.environment.start()
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "workspace-boundary.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        import_all_models()
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tmp.cleanup()
        self.environment.stop()

    async def seed_owner(self):
        async with self.sessions() as db:
            user = AppUser(email="owner@example.com", display_name="Aaryan", active=True)
            tenant = Tenant(name="ANHITRA", slug="anhitra")
            db.add_all([user, tenant])
            await db.flush()
            membership = TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner")
            db.add(membership)
            await db.commit()
            return user.id, tenant.id

    async def auth_context(self, db, user_id: str, tenant_id: str) -> AuthContext:
        user = await db.get(AppUser, user_id)
        tenant = await db.get(Tenant, tenant_id)
        membership = await db.scalar(
            select(TenantMember).where(
                TenantMember.user_id == user_id,
                TenantMember.tenant_id == tenant_id,
            )
        )
        return AuthContext(user=user, tenant=tenant, role=membership.role, session=None)

    async def test_business_context_does_not_preload_workspace_records(self):
        user_id, tenant_id = await self.seed_owner()
        async with self.sessions() as db:
            user = await db.get(AppUser, user_id)
            db.add_all(
                [
                    Memory(tenant_id=tenant_id, kind="secret", content="private-memory-marker"),
                    Task(tenant_id=tenant_id, title="private-task-marker", status="open"),
                    Message(
                        tenant_id=tenant_id,
                        channel_id=1,
                        message_id=1001,
                        author_id=1,
                        author_name=user.display_name,
                        content="private-message-marker",
                        is_bot=False,
                    ),
                ]
            )
            await db.commit()
            prompt = await load_business_context(db, tenant_id)

        self.assertIn("Workspace: ANHITRA", prompt)
        self.assertIn("No business records are automatically included", prompt)
        self.assertNotIn("private-memory-marker", prompt)
        self.assertNotIn("private-task-marker", prompt)
        self.assertNotIn("private-message-marker", prompt)

    async def test_scoped_context_injects_trusted_current_principal(self):
        user_id, tenant_id = await self.seed_owner()
        async with self.sessions() as db:
            loaded = await ContextService.load_for_agent(
                db,
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id="conversation-1",
                allow_tenant_context=True,
                query="who am I",
            )

        prompt = loaded.as_prompt()
        self.assertIn("CURRENT OPERLY SESSION", prompt)
        self.assertIn("Authenticated actor: Aaryan", prompt)
        self.assertIn("Workspace: ANHITRA", prompt)
        self.assertIn("Workspace role: owner", prompt)
        self.assertIn("Workspace context authorized: yes", prompt)

    async def test_execution_context_ignores_spoofed_role_metadata(self):
        owner_id, tenant_id = await self.seed_owner()
        async with self.sessions() as db:
            employee = AppUser(
                email="employee@example.com",
                display_name="Employee",
                active=True,
            )
            db.add(employee)
            await db.flush()
            db.add(TenantMember(tenant_id=tenant_id, user_id=employee.id, role="employee"))
            await db.commit()
            execution = await resolve_execution_context(
                db,
                workspace_id=tenant_id,
                user_id=employee.id,
                channel="discord",
                metadata={"role": "owner", "user_id": owner_id},
            )

        self.assertEqual(execution.role, "employee")
        self.assertNotIn("gmail:read", execution.permissions)
        self.assertNotIn("workspace:roles:manage", execution.permissions)

    async def test_unknown_channel_space_creates_only_a_provisional_guest_workspace(self):
        async with self.sessions() as db:
            resolution = await ChannelService.resolve(
                db,
                ChannelEnvelope(
                    provider="discord",
                    external_user_id="unlinked-user",
                    external_space_id="unknown-server",
                    external_conversation_id="general",
                    actor_name="Unknown",
                    text="hello",
                ),
            )
            installation = await db.scalar(
                select(ChannelInstallation).where(
                    ChannelInstallation.external_space_id == "unknown-server"
                )
            )
            installation_count = await db.scalar(
                select(func.count(ChannelInstallation.id))
            )

        self.assertIsNotNone(resolution.tenant_id)
        self.assertTrue(resolution.is_guest_workspace)
        self.assertTrue(resolution.principal_id.startswith("guest:"))
        self.assertIsNotNone(installation)
        self.assertTrue(installation.provisional)
        self.assertEqual(installation.tenant_id, resolution.tenant_id)
        self.assertEqual(int(installation_count or 0), 1)
        self.assertNotIn("gmail:read", resolution.effective_permissions)
        self.assertNotIn("workspace:roles:manage", resolution.effective_permissions)

    async def test_authenticated_user_can_create_an_additional_workspace(self):
        user_id, tenant_id = await self.seed_owner()
        async with self.sessions() as db:
            auth = await self.auth_context(db, user_id, tenant_id)
            result = await create_workspace(
                WorkspaceCreateInput(name="NAYSCHOOL", timezone="UTC"),
                auth,
                db,
            )
            created_membership = await db.scalar(
                select(TenantMember).where(
                    TenantMember.user_id == user_id,
                    TenantMember.tenant_id == result["id"],
                )
            )

        self.assertEqual(result["name"], "NAYSCHOOL")
        self.assertEqual(result["role"], "owner")
        self.assertFalse(result["current"])
        self.assertIsNotNone(created_membership)
        self.assertEqual(created_membership.role, "owner")

    async def test_custom_workspace_role_is_deny_by_default_except_configured_permissions(self):
        user_id, tenant_id = await self.seed_owner()
        async with self.sessions() as db:
            auth = await self.auth_context(db, user_id, tenant_id)
            role = await create_workspace_role(
                WorkspaceRoleCreateInput(
                    name="Travel Agent",
                    key="travel-agent",
                    permissions=["crm:read", "tasks:read"],
                ),
                auth,
                db,
            )
            permissions = await resolve_workspace_permissions(
                db,
                tenant_id=tenant_id,
                role=role["key"],
            )

        self.assertEqual(permissions, {"crm:read", "tasks:read"})
        self.assertNotIn("gmail:read", permissions)
        self.assertNotIn("website:write", permissions)
        self.assertNotIn("workspace:roles:manage", permissions)

    async def test_owner_can_add_member_and_assign_workspace_custom_role(self):
        owner_id, tenant_id = await self.seed_owner()
        async with self.sessions() as db:
            teammate = AppUser(
                email="teammate@example.com",
                display_name="Teammate",
                active=True,
            )
            db.add(teammate)
            await db.commit()
            auth = await self.auth_context(db, owner_id, tenant_id)
            await create_workspace_role(
                WorkspaceRoleCreateInput(
                    name="Travel Agent",
                    key="travel-agent",
                    permissions=["crm:read"],
                ),
                auth,
                db,
            )
            added = await add_workspace_member(
                WorkspaceMemberAddInput(
                    email="teammate@example.com",
                    role="employee",
                ),
                auth,
                db,
            )
            changed = await set_workspace_member_role(
                added["user_id"],
                WorkspaceMemberRoleInput(role="travel-agent"),
                auth,
                db,
            )
            permissions = await resolve_workspace_permissions(
                db,
                tenant_id=tenant_id,
                role=changed["role"],
            )

        self.assertEqual(changed["role"], "travel-agent")
        self.assertEqual(permissions, {"crm:read"})


if __name__ == "__main__":
    unittest.main()
