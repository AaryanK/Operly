import json
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import desc,select
from sqlalchemy.ext.asyncio import AsyncSession
from apps.api.dependencies import AuthContext,get_auth_context,get_db
from packages.business_brain import AgentInput,get_agent_service
from packages.business_brain.ollama_client import OllamaError
from packages.dashboard_studio.registry import get_component,screen_manifest
from packages.dashboard_studio.schemas import ChangeSetInput,ContextEnvelope,ProposalInput
from packages.dashboard_studio.service import DashboardStudioError,DashboardStudioService,operations_from_request
from packages.database.dashboard_studio_models import DashboardChangeOperation,AppConfigurationVersion

router=APIRouter(prefix="/api/dashboard-studio",tags=["dashboard-studio"]);service=DashboardStudioService()
def fail(error):
    if isinstance(error,PermissionError):return HTTPException(403,str(error))
    if isinstance(error,LookupError):return HTTPException(404,str(error))
    return HTTPException(422,str(error))
def change_out(row,operations=None):
    return {"id":row.id,"screen_id":row.screen_id,"target_component_ids":json.loads(row.target_component_ids_json),"operations":operations or [],"before":json.loads(row.before_json),"after":json.loads(row.after_json),"explanation":row.explanation,"validation":json.loads(row.validation_json),"status":row.status,"created_at":row.created_at.isoformat(),"applied_version_id":row.applied_version_id}
@router.get("/screens/{screen_id}")
async def screen(screen_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):return await service.effective_screen(db,auth.tenant.id,screen_id,auth.role)
@router.get("/screens/{screen_id}/components")
async def components(screen_id:str,auth:AuthContext=Depends(get_auth_context)):return screen_manifest(screen_id)
@router.post("/change-sets")
async def create_change_set(payload:ChangeSetInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:row=await service.create_change_set(db,auth.tenant.id,auth.user.id,auth.role,payload);return change_out(row,[x.model_dump() for x in payload.operations])
    except (DashboardStudioError,PermissionError) as e:raise fail(e)
@router.post("/chat")
async def contextual_chat(payload:ProposalInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    context=payload.context
    if context.workspace_id!=auth.tenant.id or context.user_role!=auth.role:raise HTTPException(403,"Context does not match authenticated workspace")
    selected=[]
    for supplied in context.selected_components:
        component=get_component(supplied.id)
        if not component or component.page_id not in {context.screen_id,"global"}:raise HTTPException(422,"Unknown selected component")
        selected.append(component)
    actions=[];proposal=None
    if context.mode=="customize" and selected:
        try:
            operations=operations_from_request(payload.message,selected)
            cs=await service.create_change_set(db,auth.tenant.id,auth.user.id,auth.role,ChangeSetInput(screen_id=context.screen_id,originating_chat_message=payload.message,explanation=f"Proposed dashboard customization: {payload.message[:400]}",operations=operations))
            proposal=change_out(cs,[x.model_dump() for x in operations]);actions=[{"type":"OPEN_CHANGE_SET","change_set_id":cs.id}]
        except (DashboardStudioError,PermissionError) as e:
            if isinstance(e,PermissionError):raise fail(e)
    try:
        result=await get_agent_service().run(AgentInput(tenant_id=auth.tenant.id,principal_id=f"web-user:{auth.user.id}",actor_name=auth.user.display_name,channel="web",conversation_id=payload.conversation_id,text=payload.message,metadata={"dashboard_context":context.model_dump(mode="json")}))
        message=result["message"]
        conversation_id=result["conversation_id"]
    except (OllamaError,RuntimeError) as e:
        if not proposal:raise HTTPException(503,getattr(e,"public_message","AI service is not configured"))
        message="I prepared a validated dashboard change proposal for review."
        conversation_id=payload.conversation_id
    if proposal:message += "\n\nReview the proposed change before previewing or applying it."
    return {"conversation_id":conversation_id,"message":message,"actions":actions,"change_set":proposal}
@router.get("/change-sets/{change_set_id}")
async def get_change_set(change_set_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:row=await service.change_set(db,auth.tenant.id,change_set_id)
    except LookupError as e:raise fail(e)
    ops=(await db.scalars(select(DashboardChangeOperation).where(DashboardChangeOperation.tenant_id==auth.tenant.id,DashboardChangeOperation.change_set_id==row.id).order_by(DashboardChangeOperation.position))).all()
    return change_out(row,[{"operation":x.operation,"component_id":x.component_id,"changes":json.loads(x.changes_json)} for x in ops])
@router.post("/change-sets/{change_set_id}/validate")
async def validate_change_set(change_set_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:row=await service.change_set(db,auth.tenant.id,change_set_id);return {"valid":json.loads(row.validation_json).get("valid",False),"status":row.status}
    except LookupError as e:raise fail(e)
@router.post("/change-sets/{change_set_id}/preview")
async def preview(change_set_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:return {"change_set_id":change_set_id,"overlay":await service.preview(db,auth.tenant.id,change_set_id,auth.role),"active_mutated":False}
    except (LookupError,DashboardStudioError,PermissionError) as e:raise fail(e)
@router.post("/change-sets/{change_set_id}/apply")
async def apply(change_set_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:v=await service.apply(db,auth.tenant.id,change_set_id,auth.user.id,auth.role);return {"ok":True,"version_id":v.id,"version_number":v.version_number}
    except (LookupError,DashboardStudioError,PermissionError) as e:raise fail(e)
@router.post("/change-sets/{change_set_id}/reject")
async def reject(change_set_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:row=await service.reject(db,auth.tenant.id,change_set_id,auth.user.id,auth.role);return {"ok":True,"status":row.status}
    except (LookupError,DashboardStudioError,PermissionError) as e:raise fail(e)
@router.get("/versions")
async def versions(auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    rows=(await db.scalars(select(AppConfigurationVersion).where(AppConfigurationVersion.tenant_id==auth.tenant.id).order_by(desc(AppConfigurationVersion.version_number)).limit(100))).all()
    return [{"id":x.id,"version_number":x.version_number,"summary":x.summary,"affected_components":json.loads(x.affected_json),"originating_change_set_id":x.originating_change_set_id,"source_version_id":x.source_version_id,"author_id":x.created_by,"active":x.active,"created_at":x.created_at.isoformat()} for x in rows]
@router.post("/versions/{version_id}/rollback")
async def rollback(version_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:v=await service.rollback(db,auth.tenant.id,version_id,auth.user.id,auth.role);return {"ok":True,"version_id":v.id,"version_number":v.version_number}
    except (LookupError,DashboardStudioError,PermissionError) as e:raise fail(e)
