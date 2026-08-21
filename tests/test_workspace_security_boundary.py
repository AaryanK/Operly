import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.dependencies import AuthContext
from apps.api.schemas import WorkspaceCreateInput
from apps.api.workspace_router import create_workspace
from packages.business_brain.context_loader import load_business_context
from packages.context.service import ContextService
from packages.database.db import Base
from packages.database.models import AppUser, Memory, Message, Task, Tenant, TenantMember
from packages.database.schema import import_all_models


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
            return user, tenant, membership

    async def test_business_context_does_not_preload_workspace_records(self):
        user, tenant, _ = await self.seed_owner()
        async with self.sessions() as db:
            db.add_all(
                [
                    Memory(tenant_id=tenant.id, kind="secret", content="private-memory-marker"),
                    Task(tenant_id=tenant.id, title="private-task-marker", status="open"),
                    Message(
                        tenant_id=tenant.id,
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
            prompt = await load_business_context(db, tenant.id)

        self.assertIn("Workspace: ANHITRA", prompt)
        self.assertIn("No business records are automatically included", prompt)
        self.assertNotIn("private-memory-marker", prompt)
        self.assertNotIn("private-task-marker", prompt)
        self.assertNotIn("private-message-marker", prompt)

    async def test_scoped_context_injects_trusted_current_principal(self):
        user, tenant, _ = await self.seed_owner()
        async with self.sessions() as db:
            loaded = await ContextService.load_for_agent(
                db,
                tenant_id=tenant.id,
                user_id=user.id,
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

    async def test_authenticated_user_can_create_an_additional_workspace(self):
        user, tenant, membership = await self.seed_owner()
        async with self.sessions() as db:
            db_user = await db.get(AppUser, user.id)
            db_tenant = await db.get(Tenant, tenant.id)
            result = await create_workspace(
                WorkspaceCreateInput(name="NAYSCHOOL", timezone="UTC"),
                AuthContext(user=db_user, tenant=db_tenant, role=membership.role, session=None),
                db,
            )
            created_membership = await db.scalar(
                select(TenantMember).where(
                    TenantMember.user_id == user.id,
                    TenantMember.tenant_id == result["id"],
                )
            )

        self.assertEqual(result["name"], "NAYSCHOOL")
        self.assertEqual(result["role"], "owner")
        self.assertFalse(result["current"])
        self.assertIsNotNone(created_membership)
        self.assertEqual(created_membership.role, "owner")


if __name__ == "__main__":
    unittest.main()
