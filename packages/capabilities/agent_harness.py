import json
from dataclasses import dataclass
from typing import Any

from packages.actions.service import ActionService
from packages.capabilities.providers import default_registry
from packages.database.db import session_scope


ROLE_AUTHORITY={
 "owner":{"company:read","analytics:read","crm:read","crm:write","website:read","website:write","messaging:draft","messaging:send","solution:read","solution:generate"},
 "manager":{"company:read","analytics:read","crm:read","crm:write","website:read","website:write","messaging:draft","messaging:send","solution:read"},
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
    def __init__(self,registry=None): self.registry=registry or default_registry()
    def authority(self,role:str)->set[str]: return set(ROLE_AUTHORITY.get(role,ROLE_AUTHORITY["employee"]))
    def schemas(self,context:PluginInvocationContext)->list[dict[str,Any]]:
        return [item.model_tool_schema() for item in self.registry.metadata(context.tenant_id,authority=self.authority(context.role))]
    def handles(self,name:str)->bool: return any(item.id==name for item in self.registry.definitions())
    async def invoke(self,name:str,arguments:dict[str,Any],context:PluginInvocationContext,*,call_id:str|None=None)->dict[str,Any]:
        authority=self.authority(context.role)
        async with session_scope() as db:
            service=ActionService(db,self.registry,authority=authority,actor_id=context.user_id)
            try:
                definition=next(item for item in self.registry.metadata(context.tenant_id,authority=authority) if item.id==name)
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
            message=await client.chat(messages,self.schemas(context));messages.append(message)
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
