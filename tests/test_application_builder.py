import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine

from packages.database.db import Base
from packages.database import models,business_models,agent_models,operations_models,studio_models,dashboard_studio_models,application_builder_models
from packages.database.models import Tenant,AppUser,TenantMember
from packages.database.application_builder_models import ApplicationVersion
from packages.application_builder.schema import ApplicationManifest,BuilderContext,ComponentDefinition,ProposalRequest
from packages.application_builder.service import ApplicationBuilderService,BuilderError,UnsupportedRequestError,detect_intent,plan_request,safe_log_text
from packages.application_builder.ai import ApplicationBuilderAI
from packages.application_builder.renderer import render_application
from apps.api.session import LOGIN_MAX_ATTEMPTS,clear_login_attempts,login_allowed
from packages.business_brain.agent import AgentService
from packages.business_brain.types import AgentInput

class SchemaTests(unittest.TestCase):
    def context(self,scope="application",selected=None):return BuilderContext(workspaceId="t",applicationId="a",activeVersionId="v",selectionScope=scope,selectedIds=selected or [],userRole="owner")
    def test_arbitrary_code_rejected(self):
        with self.assertRaises(ValidationError):ComponentDefinition(id="x",type="Button",label="x",properties={"script":"alert(1)"})
    def test_login_rate_limit_is_bounded(self):
        email="rate-limit@app.test";clear_login_attempts(email)
        self.assertTrue(all(login_allowed(email) for _ in range(LOGIN_MAX_ATTEMPTS)));self.assertFalse(login_allowed(email));clear_login_attempts(email)
    def test_scope_ambiguity(self):
        manifest=ApplicationManifest(application={"id":"a","name":"A"})
        with self.assertRaisesRegex(BuilderError,"select"):plan_request(ProposalRequest(message="Move this below the form",context=self.context()),manifest)
    def test_theme_scope_and_component_override(self):
        manifest=ApplicationManifest(application={"id":"a","name":"A"},components=[ComponentDefinition(id="submit",type="Button",label="Submit")])
        global_plan=plan_request(ProposalRequest(message="Change the entire application to dark green and cream",context=self.context()),manifest);self.assertEqual(global_plan["after"]["theme"]["primary"],"forest");self.assertEqual(global_plan["after"]["theme"]["background"],"cream")
        local=plan_request(ProposalRequest(message="Make only this button orange",context=self.context("component",["submit"])),ApplicationManifest.model_validate(global_plan["after"]));self.assertEqual(local["after"]["components"][0]["overrides"]["primary"],"orange");self.assertEqual(local["after"]["theme"]["primary"],"forest")
    def test_authentication_is_capability_not_form_only(self):
        manifest=ApplicationManifest(application={"id":"a","name":"A"});plan=plan_request(ProposalRequest(message="Add a secure login page",context=self.context()),manifest);after=plan["after"]
        self.assertTrue({"authentication","audit","permissions"}<={x["moduleId"] for x in after["modules"]});self.assertTrue(any(x["route"]=="/login" and not x["protected"] for x in after["routes"]));self.assertTrue(any(x["route"]=="/" and x["protected"] for x in after["routes"]));self.assertNotIn("passwordHash",str(after))
    def test_known_login_phrases_are_deterministic(self):
        manifest=ApplicationManifest(application={"id":"a","name":"A"})
        for message in ["add a login page","add a secure login page","create authentication","add user login"]:
            with self.subTest(message=message):
                self.assertEqual(detect_intent(message),"secure_login")
                plan=plan_request(ProposalRequest(message=message,context=self.context()),manifest)
                self.assertFalse(any(x["operation"]=="synthesize_application" for x in plan["operations"]))
                self.assertTrue(any(page["id"]=="login" for page in plan["after"]["pages"]))
    def test_builder_log_text_redacts_credentials(self):
        self.assertNotIn("hunter2",safe_log_text("add login password=hunter2 token:abc"))
    def test_customer_application_composes_managed_crud(self):
        manifest=ApplicationManifest(application={"id":"a","name":"A"});plan=plan_request(ProposalRequest(message="Create a customer-management application with login, a customer form, and a customer table.",context=self.context()),manifest);after=plan["after"]
        self.assertTrue({"authentication","crud_entity","form","data_table"}<={x["moduleId"] for x in after["modules"]});self.assertEqual(after["entities"][0]["id"],"customer");self.assertTrue({"Form","DataTable"}<={x["type"] for x in after["components"]})
    def test_workflow_binding_is_allowlisted(self):
        manifest=ApplicationManifest(application={"id":"a","name":"A"},components=[ComponentDefinition(id="follow-up",type="Button",label="Follow up")]);plan=plan_request(ProposalRequest(message="When this is clicked, create a follow-up task.",context=self.context("component",["follow-up"])),manifest);binding=plan["after"]["workflows"][0];self.assertEqual(binding["event"],"on_click");self.assertEqual(binding["action"],"create_record")
    def test_renderer_uses_structured_ids(self):
        manifest=ApplicationManifest(application={"id":"a","name":"A"},components=[ComponentDefinition(id="button",type="Button",label="Go",properties={"text":"Go"})]);html=render_application(manifest,studio=True);self.assertIn('data-operly-component-id="button"',html);self.assertNotIn("eval(",html)
    def test_unknown_request_is_routed_to_model_planner(self):
        with self.assertRaises(UnsupportedRequestError):plan_request(ProposalRequest(message="Build a veterinary appointment system",context=self.context()),ApplicationManifest(application={"id":"a","name":"A"}))

class AIPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_arbitrary_business_case_is_validated(self):
        class Client:
            async def chat(self,messages,tools):
                return {"content":'{"schemaVersion":1,"application":{"id":"a","name":"Clinic"},"theme":{},"modules":[{"moduleId":"crud_entity","version":1,"configuration":{}}],"pages":[{"id":"patients","name":"Patients","route":"/patients","protected":true,"componentIds":["patients-page"]}],"regions":[],"components":[{"id":"patients-page","type":"Page","label":"Patients","parentId":null,"regionId":null,"order":0,"properties":{},"overrides":{},"hiddenFor":[],"locked":false},{"id":"patient-form","type":"Form","label":"Patient registration","parentId":"patients-page","regionId":null,"order":0,"properties":{"entityId":"patient"},"overrides":{},"hiddenFor":[],"locked":false}],"entities":[{"id":"patient","name":"Patient","fields":[{"id":"name","name":"Name","type":"text","required":true}]}],"permissions":[],"workflows":[],"integrations":[],"routes":[{"route":"/patients","protected":true}]}'}
        request=ProposalRequest(message="Build a veterinary appointment system",context=BuilderContext(workspaceId="t",applicationId="a",activeVersionId="v",selectionScope="application",userRole="owner"))
        plan=await ApplicationBuilderAI(Client()).plan(request,ApplicationManifest(application={"id":"a","name":"A"}))
        self.assertEqual(plan["operations"][0]["operation"],"synthesize_application");self.assertEqual(plan["after"]["entities"][0]["id"],"patient")

    async def test_model_cannot_inject_code(self):
        class Client:
            async def chat(self,messages,tools):return {"content":'{"schemaVersion":1,"application":{"id":"a","name":"Bad"},"components":[{"id":"x","type":"Button","label":"X","properties":{"script":"alert(1)"}}]}'}
        request=ProposalRequest(message="do something",context=BuilderContext(workspaceId="t",applicationId="a",activeVersionId="v",selectionScope="application",userRole="owner"))
        with self.assertRaisesRegex(ValueError,"automatic repair attempt"):await ApplicationBuilderAI(Client()).plan(request,ApplicationManifest(application={"id":"a","name":"A"}))
    async def test_common_module_and_nested_component_shapes_are_normalized(self):
        class Client:
            async def chat(self,messages,tools):return {"content":'{"application":{"id":"wrong","name":"Mail"},"theme":{},"modules":["authentication"],"pages":[{"id":"email","name":"Email","route":"/email","protected":true,"components":[{"id":"email-page","type":"Page","label":"Email"}]}],"components":[],"entities":[],"permissions":[],"workflows":[],"integrations":[],"routes":[],"regions":[]}'}
        request=ProposalRequest(message="add an email system",context=BuilderContext(workspaceId="t",applicationId="a",activeVersionId="v",selectionScope="application",userRole="owner"))
        plan=await ApplicationBuilderAI(Client()).plan(request,ApplicationManifest(application={"id":"a","name":"A"}))
        self.assertEqual(plan["after"]["application"]["id"],"a");self.assertEqual(plan["after"]["modules"][0]["moduleId"],"authentication");self.assertEqual(plan["after"]["pages"][0]["componentIds"],["email-page"])
    async def test_invalid_first_response_is_repaired_on_second_attempt(self):
        class Client:
            def __init__(self):self.calls=0
            async def chat(self,messages,tools):
                self.calls+=1
                if self.calls==1:return {"content":'{"modules":["not-real"]}'}
                return {"content":'{"application":{"id":"a","name":"Appointments"},"theme":{},"modules":[],"pages":[{"id":"appointments","name":"Appointments","route":"/appointments","protected":true,"componentIds":["appointments-page"]}],"regions":[],"components":[{"id":"appointments-page","type":"Page","label":"Appointments"}],"entities":[],"permissions":[],"workflows":[],"integrations":[],"routes":[]}'}
        client=Client();request=ProposalRequest(message="appointments",context=BuilderContext(workspaceId="t",applicationId="a",activeVersionId="v",selectionScope="application",userRole="owner"))
        plan=await ApplicationBuilderAI(client).plan(request,ApplicationManifest(application={"id":"a","name":"A"}))
        self.assertEqual(client.calls,2);self.assertEqual(plan["after"]["pages"][0]["id"],"appointments")

    async def test_operly_ai_known_login_never_reaches_ollama(self):
        class Service(AgentService):
            async def _get_or_create_conversation(self,request):return SimpleNamespace(id="conversation")
            async def _run_known_builder_intent(self,request,conversation,user_text,intent):return {"planner":"deterministic","intent":intent}
        with patch.dict("os.environ",{"OLLAMA_API_KEY":"test-key"}):service=Service()
        class NeverClient:
            async def chat(self,*args,**kwargs):raise AssertionError("Ollama must not run")
        service.client=NeverClient()
        result=await service.run(AgentInput(tenant_id="t",principal_id="web-user:u",actor_name="Owner",channel="web",text="add user login",metadata={"user_id":"u","role":"owner"}))
        self.assertEqual(result,{"planner":"deterministic","intent":"secure_login"})

class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False);self.service=ApplicationBuilderService()
        async with self.sessions() as db:
            self.tenant=Tenant(name="One",slug="one");self.other=Tenant(name="Other",slug="other");self.user=AppUser(email="owner@app.test",password_hash="x");db.add_all([self.tenant,self.other,self.user]);await db.flush();db.add(TenantMember(tenant_id=self.tenant.id,user_id=self.user.id,role="owner"));await db.commit()
    async def asyncTearDown(self):await self.engine.dispose()
    async def proposal(self,db,app,version,message="Add a secure login page",scope="application",selected=None):return await self.service.propose(db,self.tenant.id,self.user.id,"owner",ProposalRequest(message=message,context=BuilderContext(workspaceId=self.tenant.id,applicationId=app.id,activeVersionId=version.id,selectionScope=scope,selectedIds=selected or [],userRole="owner")))
    async def test_blank_apply_persist_stale_and_rollback(self):
        async with self.sessions() as db:
            app,base=await self.service.create(db,self.tenant.id,self.user.id,"Portal");change=await self.proposal(db,app,base);preview=await self.service.preview(db,self.tenant.id,self.user.id,change.id);self.assertEqual(change.status,"previewing");self.assertIsNotNone(preview.id)
            version=await self.service.apply(db,self.tenant.id,self.user.id,"owner",change.id);self.assertEqual(version.version_number,2);current=await self.service.current(db,self.tenant.id,app.id);self.assertTrue(any(x.id=="login" for x in current[2].pages))
            with self.assertRaises(BuilderError):await self.service.apply(db,self.tenant.id,self.user.id,"owner",change.id)
            rolled=await self.service.rollback(db,self.tenant.id,self.user.id,"owner",app.id,base.id);self.assertEqual(rolled.version_number,3);self.assertFalse((await self.service.current(db,self.tenant.id,app.id))[2].pages)
    async def test_workspace_isolation(self):
        async with self.sessions() as db:
            app,version=await self.service.create(db,self.tenant.id,self.user.id,"Private")
            with self.assertRaises(LookupError):await self.service.current(db,self.other.id,app.id)
    async def test_atomic_validation_failure_creates_no_version(self):
        async with self.sessions() as db:
            app,base=await self.service.create(db,self.tenant.id,self.user.id,"Atomic");change=await self.proposal(db,app,base);change.after_json='{"invalid":true}';await db.commit()
            with self.assertRaises(ValidationError):await self.service.apply(db,self.tenant.id,self.user.id,"owner",change.id)
            rows=(await db.scalars(select(ApplicationVersion).where(ApplicationVersion.application_id==app.id))).all();self.assertEqual(len(rows),1)
    async def test_login_alias_preview_and_apply(self):
        async with self.sessions() as db:
            app,base=await self.service.create(db,self.tenant.id,self.user.id,"Aliases")
            change=await self.proposal(db,app,base,"add a login page")
            preview=await self.service.preview(db,self.tenant.id,self.user.id,change.id)
            self.assertEqual(preview.application_id,app.id)
            await self.service.apply(db,self.tenant.id,self.user.id,"owner",change.id)
            manifest=(await self.service.current(db,self.tenant.id,app.id))[2]
            self.assertTrue(any(page.id=="login" for page in manifest.pages))

class FrontendContractTests(unittest.TestCase):
    def test_canvas_layers_scope_and_viewports(self):
        source=Path("apps/web/static/studio.js").read_text(encoding="utf-8")
        for token in ["OPERLY_SELECT","event.data.multi","selectionScope","selectedIds","componentTree","desktop","tablet","mobile","Apply atomically"]:self.assertIn(token,source)
    def test_application_shell_has_mobile_navigation_and_builder_breakpoints(self):
        html=Path("apps/web/static/index.html").read_text(encoding="utf-8");app=Path("apps/web/static/app.js").read_text(encoding="utf-8");styles=Path("apps/web/static/styles.css").read_text(encoding="utf-8");studio=Path("apps/web/static/studio.css").read_text(encoding="utf-8")
        for token in ["mobile-nav-toggle","mobile-nav-backdrop","aria-controls=\"sidebar\""]:self.assertIn(token,html)
        self.assertIn("setMobileNavigation",app);self.assertIn(".sidebar.open",styles);self.assertIn("@media(max-width:700px)",studio)
    def test_public_landing_has_developer_credit(self):
        html=Path("apps/web/static/index.html").read_text(encoding="utf-8")
        landing=html.split('<div id="login"',1)[0]
        self.assertIn("Developed and maintained by <strong>Dragonzpyder Industries</strong>",landing)
        self.assertNotIn("public-footer",html.split('<div id="dashboard"',1)[1])
    def test_studio_and_ai_hide_global_dock_and_share_builder_api(self):
        app=Path("apps/web/static/app.js").read_text(encoding="utf-8")
        ai=Path("apps/web/static/ai-assistant.js").read_text(encoding="utf-8")
        studio=Path("apps/web/static/studio.js").read_text(encoding="utf-8")
        css=Path("apps/web/static/regression.css").read_text(encoding="utf-8")
        self.assertIn('page === "studio" || page === "assistant"',app)
        for bridge in ["studio-bridge.js","ai-assistant-bridge.js"]:
            self.assertIn('classList.add("page-suppressed")',Path("apps/web/static",bridge).read_text(encoding="utf-8"))
        self.assertIn(".operly-dock.page-suppressed",css)
        self.assertIn('/application-builder/proposals',studio)
        self.assertIn('/application-builder/applications',ai)
        self.assertEqual(ai.count('id="ai-input"'),1)

if __name__=="__main__":unittest.main()
