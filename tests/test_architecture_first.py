import unittest
from unittest.mock import patch
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from pydantic import ValidationError

from packages.custom_software.architectures import architecture_plan,compatibility
from packages.custom_software.planner import build_software_plan
from packages.custom_software.schema import SoftwarePlan
from packages.custom_software.plan_service import PlanConflict,approve,create_plan,revise
from packages.database.db import Base
from packages.database.models import AppUser,Tenant,TenantMember
from packages.database import models,custom_software_models,architecture_pack_models
from packages.custom_software.sandbox_jobs import create_job,transition_job
from packages.custom_software.sandbox import SandboxUnavailable,validate_runner_url
from apps.api.public_safety import PublicEndpointSafetyMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

class PlannerTests(unittest.IsolatedAsyncioTestCase):
    def test_acceptance_classification_and_negative_dispatch(self):
        cases={"Build a travel agency system where agents prepare and revise quotations and managers approve them":"quotation","Build a grocery inventory system with suppliers stock receiving low-stock alerts and purchase orders":"inventory","Build a 24-hour emergency locksmith business":"field_service","Create a membership portal with paid plans renewals events and member-only resources":"membership","Create a collaborative music composition workspace with real-time editing and audio arrangement versioning":"custom"}
        for prompt,expected in cases.items():
            with self.subTest(prompt=prompt):
                plan=build_software_plan(prompt);self.assertEqual(plan.primaryArchitecture,expected)
                if expected!="field_service":self.assertNotIn("dispatcher",plan.model_dump_json().lower());self.assertNotIn("en_route",plan.model_dump_json().lower())
        self.assertEqual(build_software_plan(cases.popitem()[0]).schemaVersion,1)

    def test_ambiguous_is_low_confidence_custom(self):
        plan=build_software_plan("Make unusual software that helps our team do a completely novel thing")
        self.assertEqual(plan.primaryArchitecture,"custom");self.assertLess(plan.confidence,.5);self.assertEqual(plan.implementationMode,"sandbox_generated")

    def test_invalid_graph_and_executable_content_rejected(self):
        data=build_software_plan("Build a grocery inventory system with stock and suppliers").model_dump();data["roles"].append(data["roles"][0].copy())
        with self.assertRaises(ValidationError):SoftwarePlan.model_validate(data)
        data=build_software_plan("Build a grocery inventory system with stock and suppliers").model_dump();data["summary"]="<script>alert(1)</script>"
        with self.assertRaises(ValidationError):SoftwarePlan.model_validate(data)

    def test_pack_compatibility_escalates_conflicts(self):
        self.assertFalse(compatibility("quotation",["field_service"])["compatible"])

    def test_runner_ssrf_policy(self):
        for url in ("http://runner.example","https://127.0.0.1","https://localhost","https://10.0.0.1"):
            with self.subTest(url=url),self.assertRaises(SandboxUnavailable):validate_runner_url(url)
        with patch.dict("os.environ",{"OPERLY_SANDBOX_RUNNER_HOSTS":"runner.example"}):
            self.assertEqual(validate_runner_url("https://runner.example"),"https://runner.example")
            with self.assertRaises(SandboxUnavailable):validate_runner_url("https://evil.example")

    async def test_public_payload_and_rate_limits(self):
        middleware=PublicEndpointSafetyMiddleware(lambda scope,receive,send:None,requests_per_minute=2,max_body_bytes=10)
        def request(length="1"):return Request({"type":"http","method":"POST","path":"/api/public/test","headers":[(b"content-length",length.encode())],"scheme":"http","server":("test",80),"client":("127.0.0.1",9),"query_string":b""})
        async def ok(_):return JSONResponse({"ok":True})
        self.assertEqual((await middleware.dispatch(request("11"),ok)).status_code,413)
        self.assertEqual((await middleware.dispatch(request(),ok)).status_code,200);self.assertEqual((await middleware.dispatch(request(),ok)).status_code,200);self.assertEqual((await middleware.dispatch(request(),ok)).status_code,429)

class PlanLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine("sqlite+aiosqlite:///:memory:");
        async with self.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        async with self.sessions() as db:
            self.tenant=Tenant(name="Plan test",slug="plan-test");self.user=AppUser(email="planner@test.local",password_hash="x",display_name="Planner");db.add_all([self.tenant,self.user]);await db.flush();db.add(TenantMember(tenant_id=self.tenant.id,user_id=self.user.id,role="owner"));await db.commit()
    async def asyncTearDown(self):await self.engine.dispose()
    async def test_revision_approval_and_stale_protection(self):
        async with self.sessions() as db:
            row,version,plan=await create_plan(db,self.tenant.id,self.user.id,"Build a travel quotation system with manager approval and itinerary review")
            version2,revised=await revise(db,row,self.user.id,"Add WhatsApp follow-up",1);self.assertEqual(version2.version,2);self.assertIn("whatsapp",revised.integrations)
            with self.assertRaises(PlanConflict):await revise(db,row,self.user.id,"Do not use payments",1)
            await approve(db,row,2);self.assertEqual(row.approved_version,2)
            with self.assertRaises(PlanConflict):await approve(db,row,1)
    async def test_mocked_sandbox_lifecycle_and_redaction(self):
        async with self.sessions() as db:
            row,_,_=await create_plan(db,self.tenant.id,self.user.id,"Create a collaborative music composition workspace with realtime editing and audio versions");await approve(db,row,1);job=await create_job(db,self.tenant.id,self.user.id,row)
            for state in ("queued","submitted","generating","installing","building","testing","previewing"):
                job=await transition_job(db,job,state)
            job=await transition_job(db,job,"completed",{"previewUrl":"https://preview.invalid","sourceArchive":"artifact.tar","testReport":"tests.json","artifactGraph":"graph.json","buildDigest":"sha256:test"});self.assertEqual(job.state,"completed")
            row2,_,_=await create_plan(db,self.tenant.id,self.user.id,"Create another collaborative music tool with realtime audio comments");await approve(db,row2,1);failed=await create_job(db,self.tenant.id,self.user.id,row2);failed=await transition_job(db,failed,"queued");failed=await transition_job(db,failed,"failed",{"message":"Bearer secret-token leaked"});self.assertNotIn("secret-token",failed.failure_message)
