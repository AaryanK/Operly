import json,unittest
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError
from sqlalchemy import func,select
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from packages.database.db import Base
from packages.database import models,business_models,agent_models,operations_models,studio_models,dashboard_studio_models
from packages.database.models import Tenant,AppUser,TenantMember
from packages.database.dashboard_studio_models import DashboardCustomization,DashboardChangeSet,AppConfigurationVersion,DashboardStudioAudit
from packages.dashboard_studio.registry import get_component
from packages.dashboard_studio.schemas import ContextEnvelope,SelectedComponent,ChangeSetInput,OperationInput
from packages.dashboard_studio.service import DashboardStudioService,DashboardStudioError,operations_from_request
from scripts.create_dev_account import ensure_development

def payload(component="overview-pending-approvals-card",changes=None):
    return ChangeSetInput(screen_id="overview",originating_chat_message="Rename this",explanation="Rename selected card",operations=[OperationInput(operation="update_component",component_id=component,changes=changes or {"title":"Approvals requiring attention"})])
class ContextTests(unittest.TestCase):
    def test_dev_account_command_refuses_production(self):
        with patch.dict("os.environ",{"OPERLY_ENV":"production","PUBLIC_BASE_URL":"https://operly.example"},clear=False):
            with self.assertRaises(RuntimeError):ensure_development()
    def test_context_envelope_validation(self):
        c=ContextEnvelope(workspace_id="tenant",route="/dashboard/overview",screen_id="overview",screen_title="Overview",mode="customize",selected_components=[],user_role="owner",viewport="desktop");self.assertEqual(c.mode,"customize")
        with self.assertRaises(ValidationError):ContextEnvelope(workspace_id="x",route="/",screen_id="x",screen_title="x",mode="root",user_role="owner",viewport="desktop")
    def test_multi_selection_request(self):
        selected=[get_component("overview-messages-card"),get_component("overview-open-tasks-card")];ops=operations_from_request("Make these the same width",selected);self.assertEqual(len(ops),2);self.assertTrue(all(x.changes["width"]=="medium" for x in ops))
    def test_visibility_request(self):
        ops=operations_from_request("Hide this from employees but keep it visible to owners and managers",[get_component("overview-messages-card")]);self.assertEqual(ops[0].changes["visibility"],["owner","manager"])
    def test_ambiguous_and_malicious_requests_do_not_create_operations(self):
        component=[get_component("overview-messages-card")]
        for request in ["Rename this.","Move these.","Run this SQL.","Delete all users.","Expose the API keys.","Apply this without approval."]:
            with self.subTest(request=request),self.assertRaises(DashboardStudioError):operations_from_request(request,component)
class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine("sqlite+aiosqlite:///:memory:");
        async with self.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False);self.svc=DashboardStudioService()
        async with self.sessions() as db:
            self.t=Tenant(name="One",slug="one");self.other=Tenant(name="Two",slug="two");self.user=AppUser(email="owner@example.com",password_hash="x");db.add_all([self.t,self.other,self.user]);await db.flush();db.add(TenantMember(tenant_id=self.t.id,user_id=self.user.id,role="owner"));await db.commit()
    async def asyncTearDown(self):await self.engine.dispose()
    async def test_unsupported_property_rejected(self):
        async with self.sessions() as db:
            with self.assertRaises(DashboardStudioError):await self.svc.create_change_set(db,self.t.id,self.user.id,"owner",payload(changes={"javascript":"alert(1)"}))
    async def test_authorization(self):
        async with self.sessions() as db:
            with self.assertRaises(PermissionError):await self.svc.create_change_set(db,self.t.id,self.user.id,"employee",payload())
    async def test_workspace_isolation(self):
        async with self.sessions() as db:
            row=await self.svc.create_change_set(db,self.t.id,self.user.id,"owner",payload())
            with self.assertRaises(LookupError):await self.svc.change_set(db,self.other.id,row.id)
            for action in (self.svc.preview,self.svc.apply,self.svc.reject):
                with self.subTest(action=action.__name__),self.assertRaises(LookupError):
                    if action==self.svc.preview:await action(db,self.other.id,row.id,"owner")
                    else:await action(db,self.other.id,row.id,self.user.id,"owner")
    async def test_state_preview_apply_version_and_persistence(self):
        async with self.sessions() as db:
            row=await self.svc.create_change_set(db,self.t.id,self.user.id,"owner",payload());self.assertEqual(row.status,"proposed")
            overlay=await self.svc.preview(db,self.t.id,row.id,"owner");self.assertEqual(overlay["overview-pending-approvals-card"]["title"],"Approvals requiring attention")
            self.assertIsNone(await db.scalar(select(DashboardCustomization).where(DashboardCustomization.tenant_id==self.t.id)))
            version=await self.svc.apply(db,self.t.id,row.id,self.user.id,"owner");self.assertEqual(row.status,"applied");self.assertEqual(version.version_number,2)
            effective=await self.svc.effective_screen(db,self.t.id,"overview","owner");card=next(x for x in effective["components"] if x["id"]=="overview-pending-approvals-card");self.assertEqual(card["effective_properties"]["title"],"Approvals requiring attention")
            self.assertEqual(await db.scalar(select(func.count(AppConfigurationVersion.id)).where(AppConfigurationVersion.tenant_id==self.t.id)),2)
    async def test_reject_transition(self):
        async with self.sessions() as db:
            row=await self.svc.create_change_set(db,self.t.id,self.user.id,"owner",payload());await self.svc.reject(db,self.t.id,row.id,self.user.id,"owner");self.assertEqual(row.status,"rejected")
            with self.assertRaises(DashboardStudioError):await self.svc.apply(db,self.t.id,row.id,self.user.id,"owner")
            self.assertEqual(await db.scalar(select(func.count(DashboardStudioAudit.id)).where(DashboardStudioAudit.action=="change_set_rejected")),1)
    async def test_duplicate_apply_is_rejected(self):
        async with self.sessions() as db:
            row=await self.svc.create_change_set(db,self.t.id,self.user.id,"owner",payload());await self.svc.apply(db,self.t.id,row.id,self.user.id,"owner")
            with self.assertRaises(DashboardStudioError):await self.svc.apply(db,self.t.id,row.id,self.user.id,"owner")
    async def test_stale_base_version_is_rejected(self):
        async with self.sessions() as db:
            first=await self.svc.create_change_set(db,self.t.id,self.user.id,"owner",payload())
            stale=await self.svc.create_change_set(db,self.t.id,self.user.id,"owner",payload(component="overview-messages-card",changes={"title":"Fresh messages"}))
            await self.svc.apply(db,self.t.id,first.id,self.user.id,"owner")
            with self.assertRaisesRegex(DashboardStudioError,"stale"):
                await self.svc.apply(db,self.t.id,stale.id,self.user.id,"owner")
    async def test_invalid_stored_override_falls_back_to_defaults(self):
        async with self.sessions() as db:
            db.add(DashboardCustomization(tenant_id=self.t.id,screen_id="overview",component_id="overview-messages-card",override_json="not-json",updated_by=self.user.id));await db.commit()
            effective=await self.svc.effective_screen(db,self.t.id,"overview","owner")
            card=next(x for x in effective["components"] if x["id"]=="overview-messages-card")
            self.assertEqual(card["effective_properties"]["title"],"Messages captured")
    async def test_role_visibility_is_computed_server_side(self):
        async with self.sessions() as db:
            change=payload(component="overview-memories-card",changes={"visibility":["owner","manager"]})
            row=await self.svc.create_change_set(db,self.t.id,self.user.id,"owner",change);await self.svc.apply(db,self.t.id,row.id,self.user.id,"owner")
            for role,expected in [("owner",True),("manager",True),("employee",False)]:
                effective=await self.svc.effective_screen(db,self.t.id,"overview",role)
                card=next(x for x in effective["components"] if x["id"]=="overview-memories-card")
                self.assertEqual(card["visible_for_role"],expected)
    async def test_rollback_creates_new_version_and_restores_defaults(self):
        async with self.sessions() as db:
            row=await self.svc.create_change_set(db,self.t.id,self.user.id,"owner",payload());await self.svc.apply(db,self.t.id,row.id,self.user.id,"owner")
            baseline=await db.scalar(select(AppConfigurationVersion).where(AppConfigurationVersion.tenant_id==self.t.id,AppConfigurationVersion.version_number==1));rolled=await self.svc.rollback(db,self.t.id,baseline.id,self.user.id,"owner");self.assertEqual(rolled.version_number,3);self.assertEqual(row.status,"rolled_back")
            self.assertIsNone(await db.scalar(select(DashboardCustomization).where(DashboardCustomization.tenant_id==self.t.id)))
class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.source=Path("apps/web/static/dashboard-customize.js").read_text(encoding="utf-8")
    def test_persistent_dock_and_navigation_context(self):self.assertIn("MutationObserver",self.source);self.assertIn("conversationId",self.source);self.assertIn("screen_title",self.source)
    def test_context_envelope_contains_only_bounded_dashboard_metadata(self):
        for field in ["workspace_id","route","screen_id","screen_title","mode","selected_components","user_role","active_app_version","viewport"]:self.assertIn(field,self.source)
        for secret in ["password_hash","api_key","session_cookie"]:self.assertNotIn(secret,self.source.lower())
    def test_selection_contract(self):self.assertIn("event.shiftKey",self.source);self.assertIn('event.key==="Escape"',self.source);self.assertIn("stopImmediatePropagation",self.source)
    def test_preview_and_apply_are_separate(self):self.assertIn("active_mutated",Path("apps/api/dashboard_studio_router.py").read_text());self.assertIn("/preview",self.source);self.assertIn("/apply",self.source)
    def test_context_chips_removable(self):self.assertIn("Remove ${text} from context",self.source);self.assertIn("builder.selected.delete",self.source)
if __name__=="__main__":unittest.main()
