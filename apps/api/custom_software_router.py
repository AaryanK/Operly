import json
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from itsdangerous import BadSignature, URLSafeSerializer
from packages.custom_software.renderer import render_dispatch, render_public, render_status
from packages.custom_software.pack_renderer import render_customer_quotation, render_inventory, render_quotation_public, render_quotation_staff
from packages.custom_software.architectures import architecture_plan, catalog as architecture_catalog
from packages.custom_software.sandbox import SandboxFailure, SandboxRunner, SandboxUnavailable, generation_plan
from packages.custom_software.schema import AgenticProjectInput, GenerateApprovedPlanInput, GenerateProjectInput, PlanApprovalInput, PlanRequestInput, PlanRevisionInput, ServiceRequestInput, TransitionInput, VisualChangeInput
from packages.custom_software.plan_service import PlanConflict, approve, create_plan, owned_plan, plan_json, plan_version, revise
from packages.custom_software.packs import pack_manifest, PACKS
from packages.custom_software.coverage import implementation_coverage
from packages.custom_software.service import ConflictError, DomainError, apply_visual_change, create_project, create_project_from_plan, create_request, list_requests, plan_artifact_graph, propose_visual_change, public_project, rollback_visual_change, transition_request
from packages.database.custom_software_models import GeneratedProject, GeneratedProjectChangeSet, ServiceRequest

router=APIRouter(tags=["custom-software"])


def status_token(request_id:str)->str:return URLSafeSerializer(os.environ["SESSION_SECRET"],salt="service-status").dumps(request_id)
def token_request_id(token:str)->str:
    try:return URLSafeSerializer(os.environ["SESSION_SECRET"],salt="service-status").loads(token)
    except BadSignature as error:raise HTTPException(404,"Status link not found") from error


@router.get("/api/custom-software/catalog/architectures")
async def architectures(auth:AuthContext=Depends(get_auth_context)):return architecture_catalog()


@router.get("/api/custom-software/catalog/packs")
async def packs(auth:AuthContext=Depends(get_auth_context)):return [pack_manifest(key) for key in PACKS]


@router.post("/api/custom-software/plans")
async def create_software_plan(payload:PlanRequestInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can create software plans")
    row,version,plan=await create_plan(db,auth.tenant.id,auth.user.id,payload.prompt)
    return plan_json(row,version,plan)


@router.get("/api/custom-software/plans/{plan_id}")
async def get_software_plan(plan_id:str,version:int|None=None,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:
        row=await owned_plan(db,auth.tenant.id,plan_id);item,plan=await plan_version(db,row,version);return plan_json(row,item,plan)
    except LookupError as error:raise HTTPException(404,str(error))


@router.post("/api/custom-software/plans/{plan_id}/revisions")
async def revise_software_plan(plan_id:str,payload:PlanRevisionInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can revise software plans")
    try:
        row=await owned_plan(db,auth.tenant.id,plan_id);version,plan=await revise(db,row,auth.user.id,payload.request,payload.expectedVersion);return plan_json(row,version,plan)
    except LookupError as error:raise HTTPException(404,str(error))
    except PlanConflict as error:raise HTTPException(409,str(error))


@router.post("/api/custom-software/plans/{plan_id}/approve")
async def approve_software_plan(plan_id:str,payload:PlanApprovalInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can approve software plans")
    try:
        row=await owned_plan(db,auth.tenant.id,plan_id);await approve(db,row,payload.expectedVersion);version,plan=await plan_version(db,row,payload.expectedVersion);return plan_json(row,version,plan)
    except LookupError as error:raise HTTPException(404,str(error))
    except PlanConflict as error:raise HTTPException(409,str(error))


@router.post("/api/custom-software/plans/{plan_id}/generate")
async def generate_approved_plan(plan_id:str,payload:GenerateApprovedPlanInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can generate custom software")
    if payload.planId!=plan_id:raise HTTPException(422,"Plan identifier mismatch")
    try:row=await owned_plan(db,auth.tenant.id,plan_id);_,plan=await plan_version(db,row,payload.approvedVersion)
    except LookupError as error:raise HTTPException(404,str(error))
    if row.approved_version!=payload.approvedVersion or row.status!="approved":raise HTTPException(409,"Generation requires the explicitly approved current plan version")
    if plan.implementationMode not in {"architecture_pack","managed_runtime"}:
        try:return await SandboxRunner().generate(plan.model_dump_json(),auth.tenant.id,auth.user.id)
        except SandboxUnavailable as error:raise HTTPException(503,detail={"code":"sandbox_not_configured","message":str(error),"planId":row.id,"approvedVersion":row.approved_version})
    coverage=implementation_coverage(plan,plan_artifact_graph(plan.model_dump(),None,1,row.approved_version))
    if not coverage["complete"]:raise HTTPException(422,detail={"code":"implementation_coverage_failed","coverage":coverage})
    project=await create_project_from_plan(db,auth.tenant.id,auth.user.id,row,plan)
    return {**project_json(project),"planId":row.id,"planVersion":row.approved_version,"coverage":coverage}


@router.get("/api/custom-software/projects/{project_id}/coverage")
async def project_coverage(project_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    project=await owned_project(db,auth,project_id)
    if not project.plan_id or not project.approved_plan_version:raise HTTPException(409,"Project is not bound to an approved SoftwarePlan")
    row=await owned_plan(db,auth.tenant.id,project.plan_id);_,plan=await plan_version(db,row,project.approved_plan_version)
    return implementation_coverage(plan,json.loads(project.artifact_graph_json))


@router.post("/api/custom-software/architecture-plan")
async def plan_architecture(payload:AgenticProjectInput,auth:AuthContext=Depends(get_auth_context)):return generation_plan(payload.prompt)


@router.post("/api/custom-software/agentic-projects",status_code=202)
async def generate_agentic(payload:AgenticProjectInput,auth:AuthContext=Depends(get_auth_context)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can start agentic generation")
    try:return await SandboxRunner().generate(payload.prompt,auth.tenant.id,auth.user.id)
    except SandboxUnavailable as error:raise HTTPException(503,detail={"code":"sandbox_not_configured","message":str(error),"plan":generation_plan(payload.prompt)})
    except SandboxFailure as error:raise HTTPException(502,detail={"code":"sandbox_failed","message":str(error)})


def project_json(row):
    return {"id":row.id,"slug":row.slug,"name":row.name,"vertical":row.vertical,"architecturePack":row.architecture_pack,"planId":row.plan_id,"approvedPlanVersion":row.approved_plan_version,"version":row.version,"publicUrl":f"/generated/{row.slug}","dispatchUrl":f"/generated/{row.slug}/dispatch" if row.architecture_pack=="field_service" else f"/generated/{row.slug}/manage","artifactGraph":json.loads(row.artifact_graph_json)}


async def owned_project(db, auth, project_id):
    row=await db.get(GeneratedProject,project_id)
    if not row or row.tenant_id!=auth.tenant.id:raise HTTPException(404,"Project not found")
    return row


@router.get("/api/custom-software/projects")
async def projects(auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    from sqlalchemy import desc, select
    rows=(await db.scalars(select(GeneratedProject).where(GeneratedProject.tenant_id==auth.tenant.id).order_by(desc(GeneratedProject.created_at)))).all()
    return [project_json(row) for row in rows]


@router.get("/api/custom-software/projects/{project_id}")
async def project(project_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    return project_json(await owned_project(db,auth,project_id))


@router.get("/api/custom-software/projects/{project_id}/preview",response_class=HTMLResponse)
async def current_preview(project_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    project=await owned_project(db,auth,project_id)
    return render_quotation_public(project) if project.architecture_pack=="quotation" else render_inventory(project) if project.architecture_pack=="inventory" else render_public(project)


@router.post("/api/custom-software/projects")
async def generate(payload:GenerateProjectInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can generate custom software")
    return project_json(await create_project(db,auth.tenant.id,auth.user.id,payload.prompt))


@router.get("/api/custom-software/projects/{project_id}/artifact-graph")
async def graph(project_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    return json.loads((await owned_project(db,auth,project_id)).artifact_graph_json)


@router.post("/api/custom-software/projects/{project_id}/visual-changes")
async def propose_visual(project_id:str,payload:VisualChangeInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can change generated software")
    try:
        row=await propose_visual_change(db,await owned_project(db,auth,project_id),auth.user.id,payload.request,payload.selected_artifact_ids,payload.viewport)
        return {"id":row.id,"baseVersion":row.base_version,"status":row.status,"selectedArtifactIds":json.loads(row.selected_artifacts_json),"after":json.loads(row.after_json),"impact":json.loads(row.impact_json)}
    except DomainError as e:raise HTTPException(422,str(e))


@router.get("/api/custom-software/projects/{project_id}/visual-changes/{change_id}/preview",response_class=HTMLResponse)
async def preview_visual(project_id:str,change_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    project=await owned_project(db,auth,project_id);change=await db.get(GeneratedProjectChangeSet,change_id)
    if not change or change.project_id!=project.id or change.tenant_id!=auth.tenant.id:raise HTTPException(404,"Change set not found")
    return render_public(project,json.loads(change.after_json))


@router.post("/api/custom-software/projects/{project_id}/visual-changes/{change_id}/apply")
async def apply_visual(project_id:str,change_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can change generated software")
    project=await owned_project(db,auth,project_id);change=await db.get(GeneratedProjectChangeSet,change_id)
    if not change:raise HTTPException(404,"Change set not found")
    try:return project_json(await apply_visual_change(db,project,change))
    except ConflictError as e:raise HTTPException(409,str(e))
    except LookupError as e:raise HTTPException(404,str(e))


@router.get("/generated/{slug}",response_class=HTMLResponse)
async def public_site(slug:str,db:AsyncSession=Depends(get_db)):
    try:
        project=await public_project(db,slug)
        if project.architecture_pack=="quotation":return render_quotation_public(project)
        if project.architecture_pack=="inventory":raise HTTPException(401,"Inventory workspace requires authentication")
        return render_public(project)
    except LookupError as e:raise HTTPException(404,str(e))

@router.post("/api/custom-software/projects/{project_id}/visual-changes/{change_id}/rollback")
async def rollback_visual(project_id:str,change_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can rollback generated software")
    project=await owned_project(db,auth,project_id);change=await db.get(GeneratedProjectChangeSet,change_id)
    if not change:raise HTTPException(404,"Change set not found")
    try:return project_json(await rollback_visual_change(db,project,change))
    except ConflictError as error:raise HTTPException(409,str(error))
    except LookupError as error:raise HTTPException(404,str(error))


@router.get("/generated/{slug}/manage",response_class=HTMLResponse)
async def manage_pack(slug:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    project=await public_project(db,slug)
    if project.tenant_id!=auth.tenant.id:raise HTTPException(404,"Project not found")
    if project.architecture_pack=="quotation":return render_quotation_staff(project)
    if project.architecture_pack=="inventory":return render_inventory(project)
    raise HTTPException(404,"Pack workspace not found")

@router.get("/quotation/customer/{token}",response_class=HTMLResponse)
async def customer_quotation_page(token:str):return render_customer_quotation(token)


@router.get("/generated/{slug}/dispatch",response_class=HTMLResponse)
async def dispatch(slug:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:
        project=await public_project(db,slug)
        if project.tenant_id!=auth.tenant.id:raise HTTPException(404,"Project not found")
        return render_dispatch(project)
    except LookupError as e:raise HTTPException(404,str(e))


@router.post("/api/public/service-projects/{slug}/requests")
async def submit(slug:str,payload:ServiceRequestInput,db:AsyncSession=Depends(get_db)):
    try:
        row,created=await create_request(db,await public_project(db,slug),payload)
        token=status_token(row.id)
        return {"id":row.id,"reference":row.reference,"status":row.status,"created":created,"statusUrl":f"/generated/{slug}/status/{row.reference}?token={token}"}
    except LookupError as e:raise HTTPException(404,str(e))


@router.get("/generated/{slug}/status/{reference}",response_class=HTMLResponse)
async def status_page(slug:str,reference:str,token:str,db:AsyncSession=Depends(get_db)):
    project=await public_project(db,slug);request_id=token_request_id(token)
    row=await db.get(ServiceRequest,request_id)
    if not row or row.project_id!=project.id or row.reference!=reference:raise HTTPException(404,"Status link not found")
    return render_status(project,reference,token)


@router.get("/api/public/service-projects/{slug}/requests/{reference}/status")
async def public_status(slug:str,reference:str,token:str,db:AsyncSession=Depends(get_db)):
    project=await public_project(db,slug);request_id=token_request_id(token);row=await db.get(ServiceRequest,request_id)
    if not row or row.project_id!=project.id or row.reference!=reference:raise HTTPException(404,"Status link not found")
    return {"reference":row.reference,"status":row.status,"assignedTo":row.assigned_to,"updatedAt":row.updated_at.isoformat()}


@router.get("/api/custom-software/projects/{project_id}/requests")
async def requests(project_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    await owned_project(db,auth,project_id)
    rows=await list_requests(db,auth.tenant.id,project_id)
    return [{"id":x.id,"reference":x.reference,"issueCategory":x.issue_category,"description":x.description,"address":x.address,"assetDetails":x.asset_details,"status":x.status,"assignedTo":x.assigned_to,"version":x.version,"createdAt":x.created_at.isoformat()} for x in rows]


@router.post("/api/custom-software/requests/{request_id}/transition")
async def transition(request_id:str,payload:TransitionInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role not in {"owner","manager"}:raise HTTPException(403,"Dispatcher permission required")
    try:
        row=await transition_request(db,auth.tenant.id,auth.user.id,request_id,payload.status,payload.expected_version,payload.assigned_to,payload.note)
        return {"id":row.id,"status":row.status,"assignedTo":row.assigned_to,"version":row.version}
    except ConflictError as e:raise HTTPException(409,str(e))
    except LookupError as e:raise HTTPException(404,str(e))
    except DomainError as e:raise HTTPException(422,str(e))
