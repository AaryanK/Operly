import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.company.events import append_event
from packages.database.db import Base
from packages.database.models import Tenant
from packages.database.schema import import_all_models
from packages.tasks.events import event_matches


class ExecutionPathProvenanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            tenant = Tenant(name="Execution Path Workspace")
            db.add(tenant)
            await db.commit()
            self.tenant_id = tenant.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_direct_human_action_records_surface_without_ai(self):
        async with self.sessions() as db:
            event = await append_event(
                db,
                tenant_id=self.tenant_id,
                event_type="action.verified",
                source="actions",
                payload={
                    "action_id": "action-direct",
                    "capability": "crm.customer.update",
                    "status": "VERIFIED",
                    "principal_id": "user:raju",
                    "origin": "slack",
                    "client_id": "slack",
                },
            )
            await db.commit()

        self.assertEqual(event.initiator_id, "user:raju")
        self.assertEqual(event.executor_id, "user:raju")
        self.assertEqual(event.execution_path["entry"]["surface"], "slack")
        self.assertEqual(event.execution_path["mediation"]["mode"], "direct")
        self.assertIsNone(event.execution_path["mediation"]["mediator"])
        self.assertEqual(
            event.execution_path["timestamp"],
            event.occurred_at.isoformat(),
        )

    async def test_ai_mediated_action_records_human_surface_and_ai(self):
        async with self.sessions() as db:
            event = await append_event(
                db,
                tenant_id=self.tenant_id,
                event_type="action.verified",
                source="actions",
                payload={
                    "action_id": "action-ai",
                    "capability": "crm.customer.update",
                    "status": "VERIFIED",
                    "principal_id": "user:raju",
                    "origin": "discord",
                    "client_id": "discord",
                },
                actor_type="agent",
                actor_id="operly:business_agent",
                initiator_type="user",
                initiator_id="user:raju",
                executor_type="agent",
                executor_id="operly:business_agent",
                delegation_chain=[
                    {
                        "from": "user:raju",
                        "to": "operly:business_agent",
                        "kind": "requested_action",
                    }
                ],
            )
            await db.commit()

        path = event.execution_path
        self.assertEqual(path["initiator"]["id"], "user:raju")
        self.assertEqual(path["entry"]["surface"], "discord")
        self.assertEqual(path["mediation"]["mode"], "ai")
        self.assertEqual(path["mediation"]["mediator"]["id"], "operly:business_agent")
        self.assertEqual(path["executor"]["id"], "operly:business_agent")
        self.assertEqual(path["action"]["capability"], "crm.customer.update")

    async def test_workflows_can_match_surface_and_ai_mediation(self):
        async with self.sessions() as db:
            event = await append_event(
                db,
                tenant_id=self.tenant_id,
                event_type="action.verified",
                source="actions",
                payload={
                    "action_id": "action-ai",
                    "capability": "messaging.send",
                    "status": "VERIFIED",
                    "principal_id": "user:raju",
                    "origin": "whatsapp",
                },
                actor_type="agent",
                actor_id="operly:business_agent",
                initiator_type="user",
                initiator_id="user:raju",
                executor_type="agent",
                executor_id="operly:business_agent",
            )
            await db.commit()

        self.assertTrue(
            event_matches(
                {
                    "kind": "event",
                    "event_id": "action.verified",
                    "where": {
                        "execution_path.entry.surface": "whatsapp",
                        "execution_path.mediation.mode": "ai",
                    },
                },
                event,
            )
        )
        self.assertFalse(
            event_matches(
                {
                    "kind": "event",
                    "event_id": "action.verified",
                    "where": {"execution_path.mediation.mode": "direct"},
                },
                event,
            )
        )


if __name__ == "__main__":
    unittest.main()
