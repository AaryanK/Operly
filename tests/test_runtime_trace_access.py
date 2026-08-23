import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.runtime_trace_router import get_conversation_runtime_trace
from packages.database.agent_models import AgentConversation
from packages.database.db import Base
from packages.database.model_trace_models import ModelRuntimeTrace
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.principal_models import Principal, PrincipalConversation


class RuntimeTraceAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "runtime-trace-access.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with self.sessions() as db:
            self.owner = AppUser(email="trace-owner@example.com", display_name="Trace Owner")
            self.other = AppUser(email="trace-other@example.com", display_name="Other User")
            self.tenant = Tenant(name="Trace Workspace")
            db.add_all([self.owner, self.other, self.tenant])
            await db.flush()
            self.membership = TenantMember(
                tenant_id=self.tenant.id,
                user_id=self.owner.id,
                role="owner",
            )
            self.workspace_conversation = AgentConversation(
                id="workspace-trace-conversation",
                tenant_id=self.tenant.id,
                principal_id=f"user:{self.owner.id}",
                channel="web",
                title="Workspace trace",
            )
            self.principal = Principal(
                kind="human",
                user_id=self.owner.id,
                display_name=self.owner.display_name,
                status="active",
            )
            db.add_all([self.membership, self.workspace_conversation, self.principal])
            await db.flush()
            self.personal_conversation = PrincipalConversation(
                principal_id=self.principal.id,
                provider="operly_web",
                external_conversation_id="discord:trace-channel",
                title="Personal trace",
                status="active",
            )
            db.add(self.personal_conversation)
            db.add_all(
                [
                    ModelRuntimeTrace(
                        run_id="run-workspace",
                        conversation_id=self.workspace_conversation.id,
                        tenant_id=self.tenant.id,
                        user_id=self.owner.id,
                        principal_id=f"user:{self.owner.id}",
                        channel="web",
                        surface="shared/workspace",
                        component="agent",
                        step=1,
                        attempt_id="attempt-workspace",
                        phase="success",
                        resource_id="test:model",
                        provider="test-provider",
                        provider_model_id="test-model",
                        attempt=1,
                        latency_ms=1,
                        payload_json="{}",
                    ),
                    ModelRuntimeTrace(
                        run_id="run-personal",
                        conversation_id="discord:trace-channel",
                        tenant_id=None,
                        user_id=self.owner.id,
                        principal_id=self.principal.id,
                        channel="discord",
                        surface="private/direct",
                        component="agent",
                        step=1,
                        attempt_id="attempt-personal",
                        phase="success",
                        resource_id="test:model",
                        provider="test-provider",
                        provider_model_id="test-model",
                        attempt=1,
                        latency_ms=1,
                        payload_json="{}",
                    ),
                ]
            )
            await db.commit()
            self.owner_id = self.owner.id
            self.other_id = self.other.id
            self.tenant_id = self.tenant.id

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tmp.cleanup()

    async def test_workspace_trace_requires_owner_principal_and_current_membership(self):
        async with self.sessions() as db:
            owner = await db.get(AppUser, self.owner_id)
            report = await get_conversation_runtime_trace(
                self.workspace_conversation.id,
                account=SimpleNamespace(user=owner),
                db=db,
            )
            self.assertEqual(report["scope"], "workspace")
            self.assertEqual(report["tenantId"], self.tenant_id)
            self.assertEqual(report["entryCount"], 1)

        async with self.sessions() as db:
            other = await db.get(AppUser, self.other_id)
            with self.assertRaises(HTTPException) as caught:
                await get_conversation_runtime_trace(
                    self.workspace_conversation.id,
                    account=SimpleNamespace(user=other),
                    db=db,
                )
            self.assertEqual(caught.exception.status_code, 404)

        async with self.sessions() as db:
            await db.execute(
                delete(TenantMember).where(
                    TenantMember.tenant_id == self.tenant_id,
                    TenantMember.user_id == self.owner_id,
                )
            )
            await db.commit()
            owner = await db.get(AppUser, self.owner_id)
            with self.assertRaises(HTTPException) as caught:
                await get_conversation_runtime_trace(
                    self.workspace_conversation.id,
                    account=SimpleNamespace(user=owner),
                    db=db,
                )
            self.assertEqual(caught.exception.status_code, 404)

    async def test_personal_trace_is_only_visible_to_owning_human(self):
        async with self.sessions() as db:
            owner = await db.get(AppUser, self.owner_id)
            report = await get_conversation_runtime_trace(
                "discord:trace-channel",
                account=SimpleNamespace(user=owner),
                db=db,
            )
            self.assertEqual(report["scope"], "personal")
            self.assertEqual(report["entryCount"], 1)

        async with self.sessions() as db:
            other = await db.get(AppUser, self.other_id)
            with self.assertRaises(HTTPException) as caught:
                await get_conversation_runtime_trace(
                    "discord:trace-channel",
                    account=SimpleNamespace(user=other),
                    db=db,
                )
            self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
