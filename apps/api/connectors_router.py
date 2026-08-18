import json,os
from datetime import datetime,timezone
from urllib.parse import urlencode
import aiohttp
from fastapi import APIRouter,Depends,HTTPException,Query
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer,BadSignature,SignatureExpired
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from apps.api.dependencies import AuthContext,get_auth_context,get_db
from packages.company.events import append_event
from packages.connectors.google_provider import GMAIL_SEND,CALENDAR,access_token,request_json
from packages.connectors.secrets import store_secret
from packages.database.connector_models import TenantConnector,ConnectorSecret
from packages.database.db import session_scope

router=APIRouter(prefix="/api/connectors",tags=["connectors"])
def serializer():return URLSafeTimedSerializer(os.environ["SESSION_SECRET"],salt="operly-google-oauth-v1")
def redirect_uri():return os.getenv("GOOGLE_OAUTH_REDIRECT_URI",os.getenv("PUBLIC_BASE_URL","http://localhost:8000").rstrip("/")+"/api/connectors/google/callback")
def owner(auth):
 if auth.role!="owner":raise HTTPException(403,"Only owners can manage connectors")

@router.get("")
async def connectors(auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
 rows=(await db.scalars(select(TenantConnector).where(TenantConnector.tenant_id==auth.tenant.id).order_by(TenantConnector.created_at))).all()
 result=[]
 for r in rows:
  scopes=json.loads(r.granted_scopes_json or "[]");result.append({"id":r.id,"provider":r.provider,"connector_type":r.connector_type,"display_name":r.display_name,"status":r.status,"enabled":r.enabled,"account":r.provider_account_id,"scopes":scopes,"capabilities":[x for x,s in (("messaging.send",GMAIL_SEND),("calendar.create_event",CALENDAR)) if s in scopes],"health_status":r.health_status,"last_health_check":r.last_health_check.isoformat() if r.last_health_check else None,"last_error":r.last_error})
 return result

@router.post("/google/connect")
async def google_connect(auth:AuthContext=Depends(get_auth_context)):
 owner(auth);state=serializer().dumps({"tenant_id":auth.tenant.id,"user_id":auth.user.id})
 scopes=["openid","email",GMAIL_SEND,CALENDAR]
 url="https://accounts.google.com/o/oauth2/v2/auth?"+urlencode({"client_id":os.environ.get("GOOGLE_OAUTH_CLIENT_ID",""),"redirect_uri":redirect_uri(),"response_type":"code","scope":" ".join(scopes),"access_type":"offline","prompt":"consent","state":state,"include_granted_scopes":"true"})
 return {"authorization_url":url}

@router.get("/google/callback")
async def google_callback(code:str=Query(...),state:str=Query(...)):
 try:data=serializer().loads(state,max_age=600)
 except (BadSignature,SignatureExpired) as e:raise HTTPException(400,"OAuth state is invalid or expired") from e
 form={"code":code,"client_id":os.environ["GOOGLE_OAUTH_CLIENT_ID"],"client_secret":os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],"redirect_uri":redirect_uri(),"grant_type":"authorization_code"}
 async with aiohttp.ClientSession() as s:
  async with s.post("https://oauth2.googleapis.com/token",data=form) as r:tokens=await r.json()
 if r.status!=200:raise HTTPException(400,"Google authorization failed")
 async with aiohttp.ClientSession() as s:
  async with s.get("https://openidconnect.googleapis.com/v1/userinfo",headers={"Authorization":f"Bearer {tokens['access_token']}"}) as r:profile=await r.json()
 tokens["expires_at"]=datetime.now(timezone.utc).timestamp()+int(tokens.get("expires_in",3600));scopes=str(tokens.get("scope","")).split()
 async with session_scope() as db:
  ref=await store_secret(db,data["tenant_id"],tokens);row=TenantConnector(tenant_id=data["tenant_id"],connector_type="google_workspace",provider="google",display_name="Google Workspace",status="connected",enabled=True,credential_reference=ref,provider_account_id=profile.get("email") or profile.get("sub"),granted_scopes_json=json.dumps(scopes),configuration_json=json.dumps({"calendar_id":"primary"}),health_status="healthy",last_health_check=datetime.utcnow());db.add(row);await db.flush();await append_event(db,tenant_id=data["tenant_id"],event_type="connector.connected",payload={"connector_id":row.id,"provider":"google","account":row.provider_account_id,"capabilities":[x for x,s in (("messaging.send",GMAIL_SEND),("calendar.create_event",CALENDAR)) if s in scopes]},actor_type="user",actor_id=data["user_id"],source="connectors")
 return RedirectResponse("/dashboard?connector=connected",303)

@router.post("/{connector_id}/disable")
async def disable(connector_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
 owner(auth);row=await db.scalar(select(TenantConnector).where(TenantConnector.id==connector_id,TenantConnector.tenant_id==auth.tenant.id));
 if not row:raise HTTPException(404,"Connector not found")
 row.enabled=False;row.status="disabled";await append_event(db,tenant_id=auth.tenant.id,event_type="connector.disabled",payload={"connector_id":row.id,"provider":row.provider},actor_type="user",actor_id=auth.user.id);await db.commit();return {"ok":True}

@router.delete("/{connector_id}")
async def disconnect(connector_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
 owner(auth);row=await db.scalar(select(TenantConnector).where(TenantConnector.id==connector_id,TenantConnector.tenant_id==auth.tenant.id));
 if not row:raise HTTPException(404,"Connector not found")
 secret=await db.get(ConnectorSecret,row.credential_reference) if row.credential_reference else None;await db.delete(row);await db.flush();
 if secret:await db.delete(secret)
 await append_event(db,tenant_id=auth.tenant.id,event_type="connector.disconnected",payload={"provider":row.provider},actor_type="user",actor_id=auth.user.id);await db.commit();return {"ok":True}

@router.post("/{connector_id}/test")
async def test_connector(connector_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
 owner(auth);row=await db.scalar(select(TenantConnector).where(TenantConnector.id==connector_id,TenantConnector.tenant_id==auth.tenant.id));
 if not row:raise HTTPException(404,"Connector not found")
 try:token=await access_token(db,row);await request_json("GET","https://www.googleapis.com/oauth2/v3/userinfo",token);row.health_status="healthy";row.last_error=None
 except Exception as e:row.health_status="failed";row.last_error=str(e)[:500];await append_event(db,tenant_id=auth.tenant.id,event_type="connector.health_failed",payload={"connector_id":row.id,"error":row.last_error})
 row.last_health_check=datetime.utcnow();await db.commit();return {"ok":row.health_status=="healthy","health_status":row.health_status,"error":row.last_error}
