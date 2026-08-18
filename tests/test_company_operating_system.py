import json
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.actions.planner import plan_business_objective
from packages.actions.policy import PolicyDecisionType, evaluate_action
from packages.actions.service import ActionService, ActionStatus
from packages.capabilities.providers import default_registry
from packages.company.context import CompanyContextRequest, build_company_context
from packages.company.events import append_event, query_events
from packages.company.state import get_company_state
from packages.database.company_models import BusinessActionRecord, BusinessEventRecord
from packages.database.custom_software_models import GeneratedProject
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.schema import import_all_models


class CompanyOperatingSystemTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection: await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.sessions()
        self.tenant = Tenant(name="Acme Local Service", timezone="America/Chicago")
        self.other = Tenant(name="Other Business")
        self.user = AppUser(email="owner@example.test", password_hash="hash")
        self.db.add_all([self.tenant, self.other, self.user]); await self.db.flush()
        self.project = GeneratedProject(tenant_id=self.tenant.id, slug="acme", name="Acme", vertical="field_service",
            prompt="Local service site", brand_json=json.dumps({"name": "Acme"}), artifact_graph_json="{}", created_by=self.user.id)
        self.db.add(self.project); await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close(); await self.engine.dispose()

    async def test_events_are_tenant_scoped_queryable_and_immutable(self):
        event = await append_event(self.db, tenant_id=self.tenant.id, event_type="company.updated",
                                   payload={"goals": ["more leads"]}, correlation_id="corr-a")
        await append_event(self.db, tenant_id=self.other.id, event_type="company.updated", payload={"secret": True})
        await self.db.commit()
        rows = await query_events(self.db, self.tenant.id, event_type="company.updated", correlation_id="corr-a")
        self.assertEqual([row.id for row in rows], [event.id]); self.assertNotIn("secret", rows[0].payload)
        record = await self.db.get(BusinessEventRecord, event.id); record.source = "tampered"
        with self.assertRaisesRegex(ValueError, "append-only"): await self.db.commit()
        await self.db.rollback()

    async def test_state_projection_and_context_filtering(self):
        await append_event(self.db, tenant_id=self.tenant.id, event_type="company.updated",
                           payload={"goals": ["more leads"], "constraints": ["owner approval"]})
        await append_event(self.db, tenant_id=self.tenant.id, event_type="website.updated", payload={"title": "old"})
        await append_event(self.db, tenant_id=self.tenant.id, event_type="customer.message.received", payload={"discord": "irrelevant"})
        state = await get_company_state(self.tenant.id, self.db)
        self.assertEqual(state.identity["name"], "Acme Local Service"); self.assertEqual(state.goals, ["more leads"])
        context = await build_company_context(CompanyContextRequest(self.tenant.id, "Improve website lead conversion"), self.db)
        self.assertIn("website", context.relevant_channels)
        self.assertNotIn("customer.message.received", [item["type"] for item in context.recent_events])

    async def test_registry_policy_and_complete_vertical_slice(self):
        registry = default_registry()
        self.assertEqual(registry.resolve(self.tenant.id, "read_analytics").name, "operly_analytics")
        plan = await plan_business_objective(self.tenant.id, "Increase incoming leads", self.db, registry)
        self.assertEqual([node["implementation_mode"] for node in plan["nodes"]], ["existing_capability", "existing_capability"])
        service = ActionService(self.db, registry)
        analytics = await service.propose(tenant_id=self.tenant.id, objective=plan["objective"], capability="read_analytics",
            arguments={}, rationale="baseline", expected_outcome="metrics", risk_level="read_only")
        website = await service.propose(tenant_id=self.tenant.id, objective=plan["objective"], capability="update_website",
            arguments={"title": "Book Acme today"}, rationale="conversion", expected_outcome="more leads", risk_level="medium")
        self.assertEqual(analytics.status, ActionStatus.VERIFIED)
        self.assertEqual(website.status, ActionStatus.WAITING_APPROVAL)
        website = await service.approve(self.tenant.id, website.id); await self.db.commit()
        self.assertEqual(website.status, ActionStatus.VERIFIED)
        self.assertTrue(json.loads(website.verification_json)["success"])
        event_types = {event.event_type for event in await query_events(self.db, self.tenant.id, limit=100)}
        self.assertTrue({"action.proposed", "action.approved", "action.executed", "action.verified"} <= event_types)
        persisted = await self.db.scalar(select(BusinessActionRecord).where(BusinessActionRecord.id == website.id))
        self.assertEqual(persisted.provider, "operly_website")
