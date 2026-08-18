import asyncio,base64,json,os
from datetime import datetime,timezone
from email.message import EmailMessage
import aiohttp
from sqlalchemy import select
from packages.capabilities.contracts import ApprovalPolicy,CapabilityDefinition,CapabilityResult,ExecutionMode
from packages.capabilities.providers import BaseProvider
from packages.company.events import append_event
from packages.connectors.secrets import read_secret,update_secret
from packages.database.business_models import Contact,Lead
from packages.database.connector_models import TenantConnector

GMAIL_SEND="https://www.googleapis.com/auth/gmail.send";CALENDAR="https://www.googleapis.com/auth/calendar.events"
class ConnectorRequired(LookupError):pass
class ProviderRejected(RuntimeError):pass

async def google_connector(db,tenant_id,scope):
 rows=(await db.scalars(select(TenantConnector).where(TenantConnector.tenant_id==tenant_id,TenantConnector.provider=="google",TenantConnector.enabled.is_(True),TenantConnector.status=="connected"))).all()
 for row in rows:
  if scope in json.loads(row.granted_scopes_json or "[]"):return row
 raise ConnectorRequired("Connect Google and grant the required scope")

async def access_token(db,connector):
 secret=await read_secret(db,connector.tenant_id,connector.credential_reference)
 if float(secret.get("expires_at",0))>datetime.now(timezone.utc).timestamp()+60:return secret["access_token"]
 data={"client_id":os.environ["GOOGLE_OAUTH_CLIENT_ID"],"client_secret":os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],"refresh_token":secret.get("refresh_token"),"grant_type":"refresh_token"}
 async with aiohttp.ClientSession() as s:
  async with s.post("https://oauth2.googleapis.com/token",data=data) as r:body=await r.json()
 if r.status!=200:raise ProviderRejected("Google authorization expired or refresh was rejected")
 secret.update(body);secret["expires_at"]=datetime.now(timezone.utc).timestamp()+int(body.get("expires_in",3600));await update_secret(db,connector.tenant_id,connector.credential_reference,secret);return secret["access_token"]

async def request_json(method,url,token,payload=None,retries=2):
 for attempt in range(retries+1):
  try:
   async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
    async with s.request(method,url,headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},json=payload) as r:
     body=await r.json(content_type=None)
     if r.status in {429,500,502,503,504} and attempt<retries:await asyncio.sleep(.2*(attempt+1));continue
     if r.status not in {200,201}:raise ProviderRejected(f"Google rejected the request ({r.status})")
     return body
  except (aiohttp.ClientError,asyncio.TimeoutError):
   if attempt>=retries:raise
   await asyncio.sleep(.2*(attempt+1))

class GmailProvider(BaseProvider):
 name="gmail"
 capabilities=(CapabilityDefinition("messaging.send","messaging_send","Send an approved lead follow-up through a connected messaging account",{"type":"object","properties":{"lead_id":{"type":"string"},"message":{"type":"string"},"subject":{"type":"string"}},"required":["lead_id","message"],"additionalProperties":False},{"type":"object"},risk_level="high",permissions=("messaging:send",),approval_policy=ApprovalPolicy.ALWAYS,execution_mode=ExecutionMode.EXTERNAL,source="external",provider="google",integration_provider="google",credential_scopes=(GMAIL_SEND,)),)
 async def execute(self,c,n,a):
  connector=await google_connector(c.db,c.tenant_id,GMAIL_SEND);lead=await c.db.scalar(select(Lead).where(Lead.id==a["lead_id"],Lead.tenant_id==c.tenant_id));contact=await c.db.get(Contact,lead.contact_id) if lead and lead.contact_id else None
  if not contact or not contact.email:return CapabilityResult(False,False,{"reason":"lead_has_no_email"})
  msg=EmailMessage();msg["To"]=contact.email;msg["Subject"]=a.get("subject") or f"Following up: {lead.title}";msg["Message-ID"]=f"<{c.execution_id}@operly.local>";msg.set_content(a["message"])
  # Gmail has no idempotency-key parameter. Do not retry an ambiguous send timeout.
  token=await access_token(c.db,connector);body=await request_json("POST","https://gmail.googleapis.com/gmail/v1/users/me/messages/send",token,{"raw":base64.urlsafe_b64encode(msg.as_bytes()).decode()},retries=0)
  lead.next_action=f"Follow-up accepted by Gmail ({body['id']})";e={"provider":"gmail","provider_account":connector.provider_account_id,"message_id":body["id"],"thread_id":body.get("threadId"),"recipient":contact.email,"provider_status":"accepted","delivery":"unknown","submitted_at":datetime.now(timezone.utc).isoformat()};await append_event(c.db,tenant_id=c.tenant_id,event_type="message.sent",payload=e,source="gmail");return CapabilityResult(True,True,e,body["id"])
 async def verify(self,c,n,a,r):return CapabilityResult(bool(r.evidence.get("message_id") and r.evidence.get("provider_status")=="accepted"),r.changed,r.evidence)

class GoogleCalendarProvider(BaseProvider):
 name="google_calendar"
 capabilities=(CapabilityDefinition("calendar.create_event","calendar_create_event","Create an approved event through a connected calendar",{"type":"object","properties":{"summary":{"type":"string"},"start":{"type":"string"},"end":{"type":"string"},"attendees":{"type":"array"},"lead_id":{"type":"string"}},"required":["summary","start","end"],"additionalProperties":False},{"type":"object"},risk_level="high",permissions=("calendar:write",),approval_policy=ApprovalPolicy.ALWAYS,execution_mode=ExecutionMode.EXTERNAL,source="external",provider="google",integration_provider="google",credential_scopes=(CALENDAR,)),)
 async def execute(self,c,n,a):
  connector=await google_connector(c.db,c.tenant_id,CALENDAR);calendar_id=json.loads(connector.configuration_json or "{}").get("calendar_id","primary");payload={"id":(c.execution_id or "").replace("-","")[:32] or None,"summary":a["summary"],"start":{"dateTime":a["start"]},"end":{"dateTime":a["end"]},"attendees":[{"email":x} for x in a.get("attendees",[])]};body=await request_json("POST",f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",await access_token(c.db,connector),payload)
  if a.get("lead_id"):
   lead=await c.db.scalar(select(Lead).where(Lead.id==a["lead_id"],Lead.tenant_id==c.tenant_id));
   if lead:lead.next_action=f"Calendar follow-up created ({body['id']})"
  e={"provider":"google_calendar","event_id":body["id"],"calendar_id":calendar_id,"start":a["start"],"end":a["end"],"attendees":a.get("attendees",[]),"provider_status":body.get("status","confirmed")};await append_event(c.db,tenant_id=c.tenant_id,event_type="calendar.event_created",payload=e,source="google_calendar");return CapabilityResult(True,True,e,body["id"])
 async def verify(self,c,n,a,r):return CapabilityResult(bool(r.evidence.get("event_id")),r.changed,r.evidence)
