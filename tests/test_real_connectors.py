import json,os,unittest
from datetime import datetime,timedelta
from unittest.mock import AsyncMock,patch
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from sqlalchemy.pool import StaticPool
from packages.actions.service import ActionService,ActionStatus
from packages.capabilities.agent_harness import ROLE_AUTHORITY
from packages.capabilities.providers import default_registry
from packages.connectors.google_provider import GMAIL_SEND,CALENDAR
from packages.connectors.secrets import store_secret
from packages.database.business_models import Contact,Lead
from packages.database.connector_models import TenantConnector
from packages.database.db import Base
from packages.database.models import AppUser,Tenant
from packages.database.schema import import_all_models

class RealConnectorTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  import_all_models();self.engine=create_async_engine("sqlite+aiosqlite:///:memory:",poolclass=StaticPool)
  async with self.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
  self.sessions=async_sessionmaker(self.engine,expire_on_commit=False);self.db=self.sessions();self.key=Fernet.generate_key().decode();self.env=patch.dict(os.environ,{"OPERLY_CONNECTOR_SECRET_KEY":self.key});self.env.start()
  self.tenant=Tenant(name="Real SMB");self.other=Tenant(name="Other");self.user=AppUser(email="owner@real.test",password_hash="x");self.db.add_all([self.tenant,self.other,self.user]);await self.db.flush()
  self.contact=Contact(tenant_id=self.tenant.id,name="Alex",email="alex@example.test");self.db.add(self.contact);await self.db.flush();self.lead=Lead(tenant_id=self.tenant.id,contact_id=self.contact.id,title="Roof estimate",stage="new",created_at=datetime.utcnow()-timedelta(days=8));self.db.add(self.lead);await self.db.commit()
 async def asyncTearDown(self):self.env.stop();await self.db.close();await self.engine.dispose()
 async def connect(self,scopes=(GMAIL_SEND,CALENDAR),enabled=True,status="connected"):
  ref=await store_secret(self.db,self.tenant.id,{"access_token":"secret-token","refresh_token":"refresh","expires_at":9999999999})
  row=TenantConnector(tenant_id=self.tenant.id,connector_type="google_workspace",provider="google",display_name="Google Workspace",status=status,enabled=enabled,credential_reference=ref,provider_account_id="owner@real.test",granted_scopes_json=json.dumps(scopes),configuration_json='{"calendar_id":"primary"}',health_status="healthy");self.db.add(row);await self.db.flush();return row
 def service(self):return ActionService(self.db,default_registry({"messaging.send","calendar.create_event"}),authority=ROLE_AUTHORITY["owner"],actor_id=self.user.id)
 async def test_tenant_isolation_disabled_and_dynamic_resolution(self):
  row=await self.connect(enabled=False);await self.db.commit();found=await self.db.scalar(select(TenantConnector).where(TenantConnector.tenant_id==self.other.id));self.assertIsNone(found)
  with self.assertRaisesRegex(LookupError,"disabled"):default_registry(set()).resolve(self.tenant.id,"messaging.send")
  self.assertEqual(default_registry({"messaging.send"}).resolve(self.tenant.id,"messaging.send").name,"gmail")
 async def test_gmail_approved_send_persists_provider_evidence_and_lead_action(self):
  await self.connect();service=self.service();action=await service.propose(tenant_id=self.tenant.id,objective="follow up stale leads",capability="messaging.send",arguments={"lead_id":self.lead.id,"message":"Still interested?"},rationale="stale lead",expected_outcome="reply",risk_level="high");self.assertEqual(action.status,ActionStatus.WAITING_APPROVAL)
  with patch("packages.connectors.google_provider.access_token",AsyncMock(return_value="hidden")),patch("packages.connectors.google_provider.request_json",AsyncMock(return_value={"id":"gmail-123","threadId":"thread-9"})):
   action=await service.approve(self.tenant.id,action.id)
  self.assertEqual(action.status,ActionStatus.VERIFIED);e=json.loads(action.result_json)["evidence"];self.assertEqual(e["message_id"],"gmail-123");self.assertEqual(e["delivery"],"unknown");self.assertNotIn("hidden",json.dumps(e));self.assertIn("gmail-123",self.lead.next_action)
 async def test_calendar_approved_creation_and_evidence(self):
  await self.connect();service=self.service();action=await service.propose(tenant_id=self.tenant.id,objective="schedule follow-up",capability="calendar.create_event",arguments={"summary":"Lead call","start":"2026-08-20T14:00:00-05:00","end":"2026-08-20T14:30:00-05:00","attendees":["alex@example.test"],"lead_id":self.lead.id},rationale="requested call",expected_outcome="calendar event",risk_level="high")
  with patch("packages.connectors.google_provider.access_token",AsyncMock(return_value="hidden")),patch("packages.connectors.google_provider.request_json",AsyncMock(return_value={"id":"event-123","status":"confirmed"})):
   action=await service.approve(self.tenant.id,action.id)
  self.assertEqual(action.status,ActionStatus.VERIFIED);self.assertEqual(json.loads(action.result_json)["evidence"]["event_id"],"event-123");self.assertIn("event-123",self.lead.next_action)
 async def test_missing_scope_provider_failure_and_approval_payload_binding(self):
  await self.connect(scopes=(CALENDAR,));service=self.service();action=await service.propose(tenant_id=self.tenant.id,objective="send",capability="messaging.send",arguments={"lead_id":self.lead.id,"message":"A"},rationale="r",expected_outcome="x",risk_level="high");action.arguments_json=json.dumps({"lead_id":self.lead.id,"message":"B"})
  action=await service.approve(self.tenant.id,action.id);self.assertEqual(action.status,ActionStatus.FAILED)
