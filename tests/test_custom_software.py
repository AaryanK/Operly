import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.custom_software.schema import ServiceRequestInput
from packages.custom_software.renderer import render_public
from packages.custom_software.service import ConflictError, DomainError, apply_visual_change, choose_brand, create_project, create_request, propose_visual_change, transition_request
from packages.database.db import Base
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.custom_software_models import ServiceCustomer, ServiceRequest, ServiceStatusEvent
from packages.database import models, custom_software_models
from apps.api.custom_software_router import status_token, token_request_id
from packages.custom_software.architectures import architecture_plan, catalog
from packages.custom_software.sandbox import SandboxRunner, SandboxUnavailable, generation_plan


class BrandTests(unittest.TestCase):
    def test_acceptance_prompts_have_distinct_brands(self):
        prompts=["Create a complete website for a bicycle rescue company", "Create a mobile auto-glass repair service", "Create an emergency pet transport service", "Build a 24-hour emergency locksmith business", "Build a mobile tire replacement company", "Create an HVAC repair dispatch application", "Build an on-site IT and network support company", "Create a commercial cleaning crew dispatch business"]
        brands=[choose_brand(x) for x in prompts]
        self.assertEqual({x["vertical"] for x in brands},{"bicycle","auto_glass","pet_transport","locksmith","mobile_tire","hvac","field_it","commercial_cleaning"})
        self.assertEqual(len({x["headline"] for x in brands}),8)
        self.assertEqual(len({x["primary"] for x in brands}),8)

    def test_customer_status_tokens_are_signed(self):
        with patch.dict("os.environ",{"SESSION_SECRET":"test-only-secret"}):
            token=status_token("request-id")
            self.assertEqual(token_request_id(token),"request-id")
            with self.assertRaises(HTTPException):token_request_id(token+"tampered")

    def test_business_architecture_families_are_classified(self):
        cases={"Build appointment booking for a salon":"booking","Create an online store with checkout and orders":"commerce","Build a club membership portal":"membership","Track warehouse inventory and purchase orders":"inventory","Create a CRM sales pipeline":"crm","Build customer quotation and estimate software":"quotation","Create a two-sided vendor marketplace":"marketplace","Build a requests and approvals workflow":"approval"}
        self.assertEqual({row["id"] for row in catalog()},{"field_service","booking","commerce","membership","inventory","crm","quotation","marketplace","approval"})
        for prompt,family in cases.items():self.assertEqual(architecture_plan(prompt)["family"],family)

    def test_agentic_generation_plan_is_bounded(self):
        plan=generation_plan("Create an online store with checkout and orders")
        self.assertEqual(plan["framework"],"nextjs-postgres");self.assertEqual(plan["policy"]["network"],"deny_by_default");self.assertFalse(plan["policy"]["productionDeploy"])


class ServiceDomainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        async with self.sessions() as db:
            self.tenant=Tenant(name="Test",slug="test");self.user=AppUser(email="owner@test.local",password_hash="x",display_name="Owner");db.add_all([self.tenant,self.user]);await db.flush();db.add(TenantMember(tenant_id=self.tenant.id,user_id=self.user.id,role="owner"));await db.commit()

    async def asyncTearDown(self):await self.engine.dispose()

    def payload(self,key="request-key-001"):
        return ServiceRequestInput(name="Alex Rider",phone="555-0100",email="alex@example.com",issue_category="Broken chain",description="Near the trail entrance",address="100 Lake Street",asset_details="Blue commuter bike",idempotency_key=key)

    async def test_public_request_is_relational_and_idempotent(self):
        async with self.sessions() as db:
            project=await create_project(db,self.tenant.id,self.user.id,"Create a complete website and app for a bicycle rescue company")
            first,created=await create_request(db,project,self.payload());second,created_again=await create_request(db,project,self.payload())
            self.assertTrue(created);self.assertFalse(created_again);self.assertEqual(first.id,second.id)
            self.assertEqual(await db.scalar(select(func.count(ServiceCustomer.id))),1);self.assertEqual(await db.scalar(select(func.count(ServiceRequest.id))),1);self.assertEqual(await db.scalar(select(func.count(ServiceStatusEvent.id))),1)
            graph=json.loads(project.artifact_graph_json);form=next(x for x in graph["nodes"] if x["id"]=="public.request-form")
            self.assertEqual(form["entity"],"service_request");self.assertIn("api",form);self.assertIn("tests",form)

    async def test_transition_state_machine_and_optimistic_version(self):
        async with self.sessions() as db:
            project=await create_project(db,self.tenant.id,self.user.id,"Create a bicycle rescue service and dispatcher")
            request,_=await create_request(db,project,self.payload("request-key-002"))
            with self.assertRaises(DomainError):await transition_request(db,self.tenant.id,self.user.id,request.id,"completed",1,None,"")
            request=await transition_request(db,self.tenant.id,self.user.id,request.id,"assigned",1,"Morgan","")
            self.assertEqual(request.version,2)
            with self.assertRaises(ConflictError):await transition_request(db,self.tenant.id,self.user.id,request.id,"en_route",1,None,"")
            request=await transition_request(db,self.tenant.id,self.user.id,request.id,"en_route",2,None,"")
            request=await transition_request(db,self.tenant.id,self.user.id,request.id,"completed",3,None,"Resolved")
            self.assertEqual(request.status,"completed");self.assertEqual(await db.scalar(select(func.count(ServiceStatusEvent.id))),4)

    async def test_tenant_boundary_is_enforced_on_transition(self):
        async with self.sessions() as db:
            project=await create_project(db,self.tenant.id,self.user.id,"Create a bicycle rescue service and dispatcher")
            request,_=await create_request(db,project,self.payload("request-key-003"))
            with self.assertRaises(LookupError):await transition_request(db,"another-tenant",self.user.id,request.id,"assigned",1,"Morgan","")

    async def test_five_more_business_projects_generate_with_artifact_graphs(self):
        prompts=["Build a 24-hour emergency locksmith business", "Build a mobile tire replacement company", "Create an HVAC repair dispatch application", "Build an on-site IT and network support company", "Create a commercial cleaning crew dispatch business"]
        async with self.sessions() as db:
            projects=[await create_project(db,self.tenant.id,self.user.id,prompt) for prompt in prompts]
            self.assertEqual(len({x.slug for x in projects}),5)
            self.assertEqual({x.vertical for x in projects},{"locksmith","mobile_tire","hvac","field_it","commercial_cleaning"})
            for project in projects:
                graph=json.loads(project.artifact_graph_json)
                self.assertEqual(graph["schemaVersion"],1)
                self.assertTrue(any(node["id"]=="public.request-form" for node in graph["nodes"]))

    async def test_selected_visual_change_versions_design_and_preserves_backend(self):
        async with self.sessions() as db:
            project=await create_project(db,self.tenant.id,self.user.id,"Create a visually distinctive bicycle rescue company")
            before_graph=project.artifact_graph_json
            change=await propose_visual_change(db,project,self.user.id,"Use a bold condensed font here, replace this with a video hero, and move the request form into a floating panel.",["public.hero","public.request-form"],"desktop")
            impact=json.loads(change.impact_json)
            self.assertEqual(set(impact["dependencies"]),{"typography.display","hero.media","request.layout"})
            self.assertIn("workflow.rescue-lifecycle",impact["preserved"])
            preview=render_public(project,json.loads(change.after_json))
            self.assertIn("font-condensed-heavy",preview);self.assertIn("media-video",preview);self.assertIn("request-floating",preview)
            project=await apply_visual_change(db,project,change)
            self.assertEqual(project.version,2);self.assertEqual(project.artifact_graph_json,before_graph)
            with self.assertRaises(ConflictError):await apply_visual_change(db,project,change)

    async def test_agentic_runner_fails_closed_without_isolation(self):
        with patch.dict("os.environ",{},clear=True):
            with self.assertRaises(SandboxUnavailable):await SandboxRunner().generate("Create an online marketplace for local vendors",self.tenant.id,self.user.id)
