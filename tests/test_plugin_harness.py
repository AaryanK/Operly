import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.actions.service import ActionService, ActionStatus
from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext, ROLE_AUTHORITY
from packages.capabilities.contracts import CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider, default_registry
from packages.capabilities.registry import CapabilityRegistry
from packages.capabilities.validation import PluginSchemaError, validate_arguments
from packages.database.business_models import BusinessDocument, Lead
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.schema import import_all_models


class FailureProvider(BaseProvider):
    name="failure"
    capabilities=(CapabilityDefinition("test.fail","test_fail","fail",{"type":"object","properties":{},"additionalProperties":False},{"type":"object"}),
                  CapabilityDefinition("test.unverified","test_unverified","unverified",{"type":"object","properties":{},"additionalProperties":False},{"type":"object"}))
    async def execute(self,context,capability_name,arguments):
        if capability_name=="test.fail":raise RuntimeError("provider exploded")
        return CapabilityResult(True,True,{"attempted":True})
    async def verify(self,context,capability_name,arguments,result):return CapabilityResult(False,result.changed,{"postcondition":False})


class FakeClient:
    def __init__(self):self.calls=0
    async def chat(self,messages,tools):
        self.calls+=1
        if self.calls==1:return {"role":"assistant","content":"","tool_calls":[{"id":"one","function":{"name":"crm.search_leads","arguments":{"stale_days":3}}}]}
        if self.calls==2:
            assert any(item.get("role")=="tool" for item in messages)
            return {"role":"assistant","content":"","tool_calls":[{"id":"two","function":{"name":"messaging.draft","arguments":{"lead_id":"lead-a","message":"Checking in"}}}]}
        return {"role":"assistant","content":"Prepared a follow-up from the observations."}


class FakeLoopHarness(PluginAgentHarness):
    async def schemas(self,context):return []
    async def invoke(self,name,arguments,context,call_id=None):return {"ok":True,"plugin":name,"arguments":arguments}


class PluginHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models();self.engine=create_async_engine("sqlite+aiosqlite:///:memory:",poolclass=StaticPool)
        async with self.engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False);self.db=self.sessions()
        self.tenant=Tenant(name="Local Pro");self.other=Tenant(name="Other");self.user=AppUser(email="plugins@example.test",password_hash="x")
        self.db.add_all([self.tenant,self.other,self.user]);await self.db.flush()
        self.lead=Lead(id="lead-a",tenant_id=self.tenant.id,title="Kitchen remodel",stage="new",value=5000,
                       created_at=datetime.utcnow()-timedelta(days=10))
        self.db.add_all([self.lead,Lead(tenant_id=self.other.id,title="Private other lead",stage="new",value=9999,
                                      created_at=datetime.utcnow()-timedelta(days=10))]);await self.db.commit()
    async def asyncTearDown(self):await self.db.close();await self.engine.dispose()

    def test_registration_resolution_authority_and_schema(self):
        registry=default_registry();self.assertEqual(registry.resolve(self.tenant.id,"crm.search_leads").name,"operly_crm")
        owner={item.id for item in registry.metadata(self.tenant.id,authority=ROLE_AUTHORITY["owner"])}
        employee={item.id for item in registry.metadata(self.tenant.id,authority=ROLE_AUTHORITY["employee"])}
        self.assertIn("messaging.send",owner);self.assertNotIn("messaging.send",employee)
        definition=next(x for x in registry.definitions() if x.id=="messaging.draft")
        with self.assertRaises(PluginSchemaError):validate_arguments(definition.input_schema,{"lead_id":"lead-a"})
        disabled=CapabilityRegistry(enabled_resolver=lambda tenant,definition: tenant!=self.tenant.id)
        disabled.register(next(provider for provider in default_registry()._providers if provider.name=="operly_crm"))
        with self.assertRaisesRegex(LookupError,"disabled"):disabled.resolve(self.tenant.id,"crm.search_leads")

    async def test_lead_conversion_auto_draft_approval_reject_and_execute(self):
        service=ActionService(self.db,default_registry(),authority=ROLE_AUTHORITY["owner"],actor_id=self.user.id)
        search=await service.propose(tenant_id=self.tenant.id,objective="convert existing leads",capability="crm.search_leads",
            arguments={"stale_days":3},rationale="find stale leads",expected_outcome="actionable leads",risk_level="read_only")
        self.assertEqual(search.status,ActionStatus.VERIFIED)
        evidence=json.loads(search.result_json)["evidence"]["leads"]
        self.assertEqual([x["id"] for x in evidence],["lead-a"])
        draft=await service.propose(tenant_id=self.tenant.id,objective="convert existing leads",capability="messaging.draft",
            arguments={"lead_id":"lead-a","message":"Are you still interested in the remodel?"},rationale="re-engage",
            expected_outcome="reply",risk_level="low")
        self.assertEqual(draft.status,ActionStatus.VERIFIED)
        send=await service.propose(tenant_id=self.tenant.id,objective="convert existing leads",capability="messaging.send",
            arguments={"lead_id":"lead-a","message":"Are you still interested in the remodel?"},rationale="re-engage",
            expected_outcome="reply",risk_level="high")
        self.assertEqual(send.status,ActionStatus.WAITING_APPROVAL)
        rejected=await service.reject(self.tenant.id,send.id);self.assertEqual(rejected.status,ActionStatus.REJECTED)
        send2=await service.propose(tenant_id=self.tenant.id,objective="convert existing leads",capability="messaging.send",
            arguments={"lead_id":"lead-a","message":"I can hold a consultation this week."},rationale="re-engage",
            expected_outcome="reply",risk_level="high")
        executed=await service.approve(self.tenant.id,send2.id);self.assertEqual(executed.status,ActionStatus.FAILED)
        self.assertIn("Connect Google",json.loads(executed.result_json)["error"])

    async def test_execution_and_verification_failures(self):
        registry=CapabilityRegistry();registry.register(FailureProvider());service=ActionService(self.db,registry)
        failed=await service.propose(tenant_id=self.tenant.id,objective="test",capability="test.fail",arguments={},rationale="test",expected_outcome="none",risk_level="low")
        unverified=await service.propose(tenant_id=self.tenant.id,objective="test",capability="test.unverified",arguments={},rationale="test",expected_outcome="none",risk_level="low")
        self.assertEqual(failed.status,ActionStatus.FAILED);self.assertEqual(unverified.status,ActionStatus.VERIFICATION_FAILED)

    async def test_model_loop_adapts_across_multiple_plugin_observations(self):
        client=FakeClient();result=await FakeLoopHarness().run_session(client,[{"role":"user","content":"convert leads"}],
            PluginInvocationContext(self.tenant.id,self.user.id,"owner","convert leads"))
        self.assertEqual(client.calls,3);self.assertEqual([x["plugin"] for x in result["trace"]],["crm.search_leads","messaging.draft"])
        self.assertIn("Prepared",result["message"])

    async def test_generated_solution_bridge_is_approval_gated(self):
        service=ActionService(self.db,default_registry(),authority=ROLE_AUTHORITY["owner"],actor_id=self.user.id)
        action=await service.propose(tenant_id=self.tenant.id,objective="connect proprietary supplier",capability="solution.generate",
            arguments={"requirement":"Create an isolated supplier status adapter"},rationale="missing capability",
            expected_outcome="verified adapter plan",risk_level="high")
        self.assertEqual(action.status,ActionStatus.WAITING_APPROVAL)
        action=await service.approve(self.tenant.id,action.id)
        self.assertEqual(action.status,ActionStatus.VERIFIED)
        self.assertTrue(json.loads(action.verification_json)["evidence"]["persisted"])
