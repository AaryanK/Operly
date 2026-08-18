import json
from dataclasses import dataclass
from typing import Any

from packages.actions.service import ActionService
from packages.capabilities.providers import default_registry
from packages.database.db import session_scope


ROLE_AUTHORITY={
 "owner":{"company:read","analytics:read","crm:read","crm:write","website:read","website:write","messaging:draft","messaging:send","calendar:write","solution:read","solution:generate"},
 "manager":{"company:read","analytics:read","crm:read","crm:write","website:read","website:write","messaging:draft","messaging:send","calendar:write","solution:read"},
 "agent":{"company:read","analytics:read","crm:read","crm:write","website:read","messaging:draft","solution:read"},
 "employee":{"company:read","analytics:read","crm:read","website:read","solution:read"},
}


@dataclass(slots=True)
class PluginInvocationContext:
    tenant_id:str
    user_id:str|None
    role:str
    objective:str


class PluginAgentHarness:
    """Execution authority between the reasoning model and every plugin."""
    def __init__(self,registry=None): self.registry=registry
    async def registry_for(self,context):
        if self.registry:return self.registry
        from sqlalchemy import select
        from packages.database.connector_models import TenantConnector
        from packages.connectors.google_provider import GMAIL_SEND,CALENDAR
        enabled=set()
        async with session_scope() as db:
            rows=(await db.scalars(select(TenantConnector).where(TenantConnector.tenant_id==context.tenant_id,TenantConnector.enabled.is_(True),TenantConnector.status=="connected"))).all()
            for row in rows:
                scopes=set(json.loads(row.granted_scopes_json or "[]"))
                if GMAIL_SEND in scopes:enabled.add("messaging.send")
                if CALENDAR in scopes:enabled.add("calendar.create_event")
        return default_registry(enabled)
    def authority(self,role:str)->set[str]: return set(ROLE_AUTHORITY.get(role,ROLE_AUTHORITY["employee"]))
    async def schemas(self,context:PluginInvocationContext)->list[dict[str,Any]]:
        registry=await self.registry_for(context);return [item.model_tool_schema() for item in registry.metadata(context.tenant_id,authority=self.authority(context.role))]
    def handles(self,name:str)->bool: return name in {"messaging.send","calendar.create_event"} or bool(self.registry and any(item.id==name for item in self.registry.definitions())) or name.count(".")==1
    async def invoke(self,name:str,arguments:dict[str,Any],context:PluginInvocationContext,*,call_id:str|None=None)->dict[str,Any]:
        authority=self.authority(context.role);registry=await self.registry_for(context)
        async with session_scope() as db:
            service=ActionService(db,registry,authority=authority,actor_id=context.user_id)
            try:
                definition=next(item for item in registry.metadata(context.tenant_id,authority=authority) if item.id==name)
            except StopIteration:
                return {"ok":False,"error":"Unknown or unauthorized plugin"}
            rationale=str(arguments.pop("_rationale","") or f"Model selected {name} for the owner objective")[:2000]
            expected=str(arguments.pop("_expected_outcome","") or definition.description)[:2000]
            try:
                action=await service.propose(tenant_id=context.tenant_id,objective=context.objective,capability=name,
                    arguments=arguments,rationale=rationale,expected_outcome=expected,risk_level=definition.risk_level,
                    causation_id=call_id,idempotency_key=f"{context.tenant_id}:{call_id}" if call_id else None)
            except (ValueError,PermissionError,LookupError) as error:
                return {"ok":False,"error":str(error)}
            await db.commit()
            result=json.loads(action.result_json or "{}")
            return {"ok":action.status in {"VERIFIED","WAITING_APPROVAL"},"action_id":action.id,"plugin":name,
                    "status":action.status,"approval_id":action.approval_id,"observation":result.get("evidence",{}),
                    "verification":json.loads(action.verification_json or "{}")}

    async def run_session(self,client,messages:list[dict[str,Any]],context:PluginInvocationContext,max_steps:int=8):
        """Reusable adaptive model loop; observations remain in the same model session."""
        trace=[]
        for _ in range(max_steps):
            message=await client.chat(messages,await self.schemas(context));messages.append(message)
            calls=message.get("tool_calls") or []
            if not calls:return {"message":message.get("content") or "Done.","trace":trace,"messages":messages}
            for call in calls:
                function=call.get("function") or {};arguments=function.get("arguments") or {}
                if isinstance(arguments,str):
                    try:arguments=json.loads(arguments)
                    except json.JSONDecodeError:arguments={}
                result=await self.invoke(str(function.get("name") or ""),arguments,context,call_id=str(call.get("id") or "") or None)
                trace.append({"plugin":function.get("name"),"observation":result})
                messages.append({"role":"tool","tool_name":function.get("name"),"content":json.dumps(result,default=str)})
        return {"message":"Stopped at the safe plugin-call limit.","trace":trace,"messages":messages}
