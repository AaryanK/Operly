import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.capabilities.action_provider import ActionLifecycleProvider
from packages.capabilities.context_provider import ContextProvider
from packages.capabilities.defaults import default_registry
from packages.capabilities.history_provider import ConversationHistoryProvider
from packages.capabilities.runtime_context import ProviderContext
from packages.database.agent_models import AgentConversation, AgentMessage
from packages.database.company_models import BusinessActionRecord
from packages.database.db import Base
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models


class DurableRuntimeStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "OPERLY_ENV": "test",
                "SESSION_SECRET": "durable-state-session-secret-that-is-long-enough",
                "AUTH_TOKEN_PEPPER": "durable-state-token-pepper-that-is-long-enough",
            },
        )
        self.environment.start()
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "durable.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        import_all_models()
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tmp.cleanup()
        self.environment.stop()

    async def seed(self):
        async with self.sessions() as db:
            user = AppUser(email="human@example.com", display_name="Human", active=True)
            alpha = Tenant(name="Alpha", slug="alpha")
            beta = Tenant(name="Beta", slug="beta")
            db.add_all([user, alpha, beta])
            await db.flush()
            db.add_all(
                [
                    TenantMember(tenant_id=alpha.id, user_id=user.id, role="owner"),
                    TenantMember(tenant_id=beta.id, user_id=user.id, role="owner"),
                ]
            )
            await db.commit()
            return user.id, alpha.id, beta.id

    async def test_global_human_memory_follows_person_but_workspace_private_does_not(self):
        user_id, alpha_id, beta_id = await self.seed()
        provider = ContextProvider()
        async with self.sessions() as db:
            alpha = ProviderContext(alpha_id, db, user_id, invocation={"channel": "discord", "metadata": {}})
            beta = ProviderContext(beta_id, db, user_id, invocation={"channel": "web", "metadata": {}})
            await provider.execute(alpha, "context.human.remember", {"content": "My codeword is ORBIT-742"})
            await provider.execute(alpha, "context.private_workspace_remember", {"content": "Alpha-only preference Saturn"})
            await db.commit()

            global_result = await provider.execute(beta, "context.human.search", {"query": "ORBIT-742"})
            private_result = await provider.execute(beta, "context.private_workspace_search", {"query": "Saturn"})

        self.assertEqual([m["content"] for m in global_result.evidence["matches"]], ["My codeword is ORBIT-742"])
        self.assertEqual(private_result.evidence["matches"], [])

    async def test_persisted_history_is_retrievable_beyond_working_window(self):
        user_id, alpha_id, _ = await self.seed()
        principal = f"user:{user_id}"
        conversation_id = "history-conversation"
        async with self.sessions() as db:
            db.add(AgentConversation(id=conversation_id, tenant_id=alpha_id, principal_id=principal, channel="discord"))
            for index in range(35):
                text = "ORBIT archival marker" if index == 0 else f"message {index}"
                db.add(AgentMessage(tenant_id=alpha_id, conversation_id=conversation_id, role="user", content=text))
            await db.commit()

            provider = ConversationHistoryProvider()
            context = ProviderContext(
                alpha_id,
                db,
                user_id,
                invocation={"channel": "discord", "metadata": {"principal_id": principal, "_conversation_id": conversation_id}},
            )
            result = await provider.execute(context, "conversation.search_history", {"query": "ORBIT archival", "limit": 10})

        self.assertTrue(result.success)
        self.assertTrue(any("ORBIT archival marker" in item["content"] for item in result.evidence["matches"]))

    async def test_recent_resources_come_from_durable_action_results(self):
        user_id, alpha_id, _ = await self.seed()
        async with self.sessions() as db:
            db.add(
                BusinessActionRecord(
                    tenant_id=alpha_id,
                    objective="Create a draft",
                    capability="gmail.create_draft",
                    status="VERIFIED",
                    result_json=json.dumps(
                        {
                            "success": True,
                            "changed": True,
                            "external_reference": "draft-123",
                            "evidence": {"draft_id": "draft-123", "message_id": "message-123"},
                        }
                    ),
                )
            )
            await db.commit()
            provider = ActionLifecycleProvider()
            context = ProviderContext(alpha_id, db, user_id, invocation={"channel": "discord", "metadata": {}})
            result = await provider.execute(context, "runtime.recent_resources", {"limit": 10})

        resources = result.evidence["resources"]
        self.assertEqual(resources[0]["external_reference"], "draft-123")
        self.assertEqual(resources[0]["identifiers"]["draft_id"], "draft-123")

    def test_registry_exposes_durable_lifecycle_capabilities(self):
        ids = {definition.id for definition in default_registry().definitions()}
        expected = {
            "actions.pending",
            "actions.approve",
            "actions.reject",
            "runtime.recent_resources",
            "conversation.search_history",
            "conversation.read_recent",
            "gmail.list_drafts",
            "gmail.get_draft",
            "gmail.update_draft",
            "gmail.send_draft",
            "gmail.delete_draft",
        }
        self.assertTrue(expected.issubset(ids))


if __name__ == "__main__":
    unittest.main()
