import json
import os

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from itsdangerous import BadSignature, URLSafeSerializer

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.coding_harness.build_service import SourceRecordError
from packages.coding_harness.execution_loop import build_with_repair
from packages.coding_harness.opencode_agent import CodingAgentNeedsUserInput, CodingHarnessError
from packages.coding_harness.source_service import source_record_json
from packages.custom_software.architectures import architecture_plan, catalog as architecture_catalog
from packages.custom_software.construction import build_preview_evidence
from packages.custom_software.coverage import implementation_coverage
from packages.custom_software.live_planning import PlannerUnavailable, PlanningBlocked
from packages.custom_software.pack_renderer import render_customer_quotation, render_inventory, render_quotation_public, render_quotation_staff
from packages.custom_software.packs import PACKS, pack_manifest
from packages.custom_software.plan_service import PlanConflict, approve, create_plan, owned_plan, plan_json, plan_version, revise
from packages.custom_software.renderer import render_dispatch, render_public, render_status
from packages.custom_software.runner_adapters import ExternalRunnerAdapter
from packages.custom_software.runner_service import RunnerStateError, active_preview, build_events, build_json, owned_build, refresh_build, stop_preview
from packages.custom_software.sandbox import SandboxFailure, SandboxRunner, SandboxUnavailable, generation_plan
from packages.custom_software.schema import AgenticProjectInput, GenerateApprovedPlanInput, GenerateProjectInput, PlanApprovalInput, PlanRequestInput, PlanRevisionInput, RunnerBuildInput, RunnerRepairInput, ServiceRequestInput, TransitionInput, VisualChangeInput
from packages.custom_software.service import ConflictError, DomainError, apply_visual_change, create_project, create_project_from_plan, create_request, list_requests, plan_artifact_graph, propose_visual_change, public_project, rollback_visual_change, transition_request
from packages.database.custom_software_models import GeneratedProject, GeneratedProjectChangeSet, PlanningModelInvocation, RunnerArtifactRecord, RunnerPreviewRecord, ServiceRequest
from packages.model_runtime import OllamaError
from urllib.parse import urlparse

router=APIRouter(tags=["custom-software"])


def status_token(request_id:str)->str:return URLSafeSerializer(os.environ["SESSION_SECRET"],salt="service-status").dumps(request_id)
def token_request_id(token:str)->str:
    try:return URLSafeSerializer(os.environ["SESSION_SECRET"],salt="service-status").loads(token)
    except BadSignature as error:raise HTTPException(404,"Status link not found") from error


def _coding_clarification(error:CodingAgentNeedsUserInput)->HTTPException:
    return HTTPException(status_code=409,detail={"code":"coding_agent_clarification_required","message":error.question,"question":error.question,"options":error.options})


async def _build_result(db:AsyncSession,auth:AuthContext,row,plan,payload:RunnerBuildInput):
    try:
        build,source,repairs=await build_with_repair(
            db,
            auth.tenant.id,
            auth.user.id,
            row,
            plan,
            payload.idempotencyKey,
            adapter=ExternalRunnerAdapter(),
        )
    except SourceRecordError as error:raise HTTPException(409,str(error)) from error
    except CodingAgentNeedsUserInput as error:raise _coding_clarification(error) from error
    except OllamaError as error:raise HTTPException(status_code=503,detail=error.public_message) from error
    except CodingHarnessError as error:raise HTTPException(status_code=422,detail={"code":"coding_harness_blocked","message":str(error)}) from error
    result=build_json(build)
    if build.state=="preview_ready":
        preview=await db.scalar(select(RunnerPreviewRecord).where(RunnerPreviewRecord.build_id==build.id,RunnerPreviewRecord.tenant_id==auth.tenant.id,RunnerPreviewRecord.state=="active"))
        if preview:result["preview"]={"id":preview.id,"url":f"/api/custom-software/previews/{preview.id}/","expiresAt":preview.expires_at.isoformat()}
    result["source"]=source_record_json(source)
    result["repairAttempts"]=repairs
    result["repairCount"]=len(repairs)
    return result


@router.get("/api/custom-software/catalog/architectures")
async def architectures(auth:AuthContext=Depends(get_auth_context)):return architecture_catalog()


@router.get("/api/custom-software/catalog/packs")
async def packs(auth:AuthContext=Depends(get_auth_context)):return [pack_manifest(key) for key in PACKS]


@router.post("/api/custom-software/plans")
async def create_software_plan(payload:PlanRequestInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can create software plans")
    try:row,version,plan=await create_plan(db,auth.tenant.id,auth.user.id,payload.prompt)
    except PlannerUnavailable as error:raise HTTPException(503,detail={"code":"planner_unavailable","message":str(error)}) from error
    except PlanningBlocked as error:raise HTTPException(422,detail={"code":"planning_blocked","message":str(error)}) from error
    return plan_json(row,version,plan)


@router.get("/api/custom-software/plans/{plan_id}")
async def get_software_plan(plan_id:str,version:int|None=None,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:
        row=await owned_plan(db,auth.tenant.id,plan_id);item,plan=await plan_version(db,row,version);return plan_json(row,item,plan)
    except LookupError as error:raise HTTPException(404,str(error)) from error


@router.post("/api/custom-software/plans/{plan_id}/revisions")
async def revise_software_plan(plan_id:str,payload:PlanRevisionInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can revise software plans")
    try:
        row=await owned_plan(db,auth.tenant.id,plan_id);version,plan=await revise(db,row,auth.user.id,payload.request,payload.expectedVersion);return plan_json(row,version,plan)
    except LookupError as error:raise HTTPException(404,str(error)) from error
    except PlanConflict as error:raise HTTPException(409,str(error)) from error


@router.post("/api/custom-software/plans/{plan_id}/approve")
async def approve_software_plan(plan_id:str,payload:PlanApprovalInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can approve software plans")
    try:
        row=await owned_plan(db,auth.tenant.id,plan_id);await approve(db,row,payload.expectedVersion);version,plan=await plan_version(db,row,payload.expectedVersion);return plan_json(row,version,plan)
    except LookupError as error:raise HTTPException(404,str(error)) from error
    except PlanConflict as error:raise HTTPException(409,str(error)) from error


@router.post("/api/custom-software/plans/{plan_id}/generate")
async def generate_approved_plan(plan_id:str,payload:GenerateApprovedPlanInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can generate custom software")
    if payload.planId!=plan_id:raise HTTPException(422,"Plan identifier mismatch")
    try:row=await owned_plan(db,auth.tenant.id,plan_id);_,plan=await plan_version(db,row,payload.approvedVersion)
    except LookupError as error:raise HTTPException(404,str(error)) from error
    if row.approved_version!=payload.approvedVersion or row.status!="approved":raise HTTPException(409,"Generation requires the explicitly approved current plan version")
    if plan.implementationMode not in {"architecture_pack","managed_runtime"}:
        try:return await SandboxRunner().generate(plan.model_dump_json(),auth.tenant.id,auth.user.id)
        except SandboxUnavailable:
            return {"planId":row.id,"approvedVersion":row.approved_version,**build_preview_evidence(plan)}
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


@router.get("/api/custom-software/plans/{plan_id}/preview-evidence")
async def preview_evidence(plan_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    row=await owned_plan(db,auth.tenant.id,plan_id)
    if row.status!="approved" or not row.approved_version:raise HTTPException(409,"Preview requires an approved plan")
    _,plan=await plan_version(db,row,row.approved_version)
    return build_preview_evidence(plan)


@router.post("/api/custom-software/builds",status_code=202)
async def create_runner_build(payload:RunnerBuildInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can submit isolated builds")
    try:row=await owned_plan(db,auth.tenant.id,payload.planId);_,plan=await plan_version(db,row,payload.approvedVersion)
    except LookupError as error:raise HTTPException(404,str(error)) from error
    if row.status!="approved" or row.approved_version!=payload.approvedVersion:raise HTTPException(409,"Build submission requires the approved plan version")
    return await _build_result(db,auth,row,plan,payload)


@router.get("/api/custom-software/plans/{plan_id}/requirements")
async def get_plan_requirements(plan_id:str,version:int|None=None,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:row=await owned_plan(db,auth.tenant.id,plan_id);_,plan=await plan_version(db,row,version);return [x.model_dump() for x in plan.requirementLedger]
    except LookupError as error:raise HTTPException(404,str(error)) from error


@router.get("/api/custom-software/plans/{plan_id}/tree")
async def get_plan_tree(plan_id:str,version:int|None=None,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:row=await owned_plan(db,auth.tenant.id,plan_id);_,plan=await plan_version(db,row,version);return {"nodes":[x.model_dump() for x in plan.planTree],"metrics":plan.planningMetrics.model_dump() if plan.planningMetrics else {},"globalValidation":plan.globalValidation,"semanticDiff":plan.semanticDiff.model_dump() if plan.semanticDiff else {}}
    except LookupError as error:raise HTTPException(404,str(error)) from error


@router.get("/api/custom-software/plans/{plan_id}/provenance")
async def get_plan_provenance(plan_id:str,version:int|None=None,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    row=await owned_plan(db,auth.tenant.id,plan_id);number=version or row.current_version
    records=(await db.scalars(select(PlanningModelInvocation).where(PlanningModelInvocation.plan_id==row.id,PlanningModelInvocation.tenant_id==auth.tenant.id,PlanningModelInvocation.plan_version==number).order_by(PlanningModelInvocation.created_at,PlanningModelInvocation.attempt))).all()
    return [{"id":x.id,"role":x.role,"nodeId":x.node_id,"planningMode":x.planning_mode,"provider":x.provider,"modelId":x.model_id,"requestId":x.request_id,"contextDigest":x.context_digest,"promptVersion":x.prompt_version,"attempt":x.attempt,"structuredOutput":json.loads(x.structured_output_json),"validationErrors":json.loads(x.validation_errors_json),"retryHistory":json.loads(x.retry_history_json),"latencyMs":x.latency_ms,"inputTokens":x.input_tokens,"outputTokens":x.output_tokens,"failureClassification":x.failure_classification,"createdAt":x.created_at.isoformat()} for x in records]


@router.get("/api/custom-software/builds/{build_id}")
async def runner_build(build_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:
        row=await owned_build(db,auth.tenant.id,build_id)
        if row.state not in {"preview_ready","failed","cleaned","cancelled","timed_out","security_blocked","resource_exceeded"}:row=await refresh_build(db,row)
        return build_json(row)
    except LookupError as error:raise HTTPException(404,str(error)) from error


@router.get("/api/custom-software/builds/{build_id}/events")
async def runner_events(build_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:row=await owned_build(db,auth.tenant.id,build_id)
    except LookupError as error:raise HTTPException(404,str(error)) from error
    return [{"sequence":x.sequence,"state":x.state,"eventType":x.event_type,"message":x.message,"details":json.loads(x.details_json),"timestamp":x.created_at.isoformat()} for x in await build_events(db,row)]


@router.get("/api/custom-software/builds/{build_id}/artifacts")
async def runner_artifacts(build_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:await owned_build(db,auth.tenant.id,build_id)
    except LookupError as error:raise HTTPException(404,str(error)) from error
    rows=(await db.scalars(select(RunnerArtifactRecord).where(RunnerArtifactRecord.build_id==build_id,RunnerArtifactRecord.tenant_id==auth.tenant.id))).all()
    return [{"id":x.id,"kind":x.kind,"name":x.name,"digest":x.digest,"sizeBytes":x.size_bytes,"reference":x.reference} for x in rows]


@router.get("/api/custom-software/builds/{build_id}/logs")
async def runner_logs(build_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:row=await owned_build(db,auth.tenant.id,build_id)
    except LookupError as error:raise HTTPException(404,str(error)) from error
    events=await build_events(db,row)
    return {"buildId":row.id,"truncated":False,"entries":[{"sequence":x.sequence,"state":x.state,"message":x.message,"details":json.loads(x.details_json)} for x in events]}


@router.post("/api/custom-software/builds/{build_id}/preview")
async def start_runner_preview(build_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:build=await owned_build(db,auth.tenant.id,build_id)
    except LookupError as error:raise HTTPException(404,str(error)) from error
    if build.state!="preview_ready":raise HTTPException(409,"A running, healthy, accepted build is required")
    preview=await db.scalar(select(RunnerPreviewRecord).where(RunnerPreviewRecord.build_id==build.id,RunnerPreviewRecord.tenant_id==auth.tenant.id,RunnerPreviewRecord.state=="active"))
    if not preview:raise HTTPException(409,"Runner did not provide an active preview")
    return {"id":preview.id,"url":f"/api/custom-software/previews/{preview.id}/","expiresAt":preview.expires_at.isoformat(),"message":"Isolated development preview — not deployed to production."}


@router.post("/api/custom-software/builds/{build_id}/cancel")
async def cancel_runner_build(build_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    from packages.custom_software.runner_service import _event
    try:row=await owned_build(db,auth.tenant.id,build_id);await _event(db,row,"cancel_requested",message="Owner requested cancellation");await ExternalRunnerAdapter().cancel(row.runner_job_id);await _event(db,row,"cancelled",message="Runner confirmed cancellation");await db.commit();return build_json(row)
    except LookupError as error:raise HTTPException(404,str(error)) from error
    except RunnerStateError as error:raise HTTPException(409,str(error)) from error


@router.post("/api/custom-software/builds/{build_id}/cleanup")
async def cleanup_runner_build(build_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    from packages.custom_software.runner_service import _event
    try:
        row=await owned_build(db,auth.tenant.id,build_id)
        if row.state not in {"cancelled","failed","build_failed","tests_failed","start_failed","health_check_failed","acceptance_failed","preview_ready","completed"}:raise HTTPException(409,"Build cannot be cleaned from its current state")
        await _event(db,row,"cleaning",message="Runner cleanup requested");await ExternalRunnerAdapter().cleanup(row.runner_job_id);await _event(db,row,"cleaned",message="Runner cleanup confirmed");await db.commit();return build_json(row)
    except LookupError as error:raise HTTPException(404,str(error)) from error
    except RunnerStateError as error:raise HTTPException(409,str(error)) from error


@router.post("/api/custom-software/builds/{build_id}/repair",status_code=202)
async def repair_runner_build(build_id:str,payload:RunnerRepairInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can request repair")
    try:
        failed=await owned_build(db,auth.tenant.id,build_id)
        row=await owned_plan(db,auth.tenant.id,failed.plan_id)
        if row.status!="approved" or not row.approved_version:raise HTTPException(409,"Repair requires an approved plan version")
        _,plan=await plan_version(db,row,row.approved_version)
        request=RunnerBuildInput(planId=row.id,approvedVersion=row.approved_version,idempotencyKey=payload.idempotencyKey)
        result=await _build_result(db,auth,row,plan,request)
        result["requestedFromBuildId"]=failed.id
        return result
    except LookupError as error:raise HTTPException(404,str(error)) from error


def _validated_preview_target(value:str)->str:
    parsed=urlparse(value);allowed={x.strip().lower() for x in os.getenv("OPERLY_SANDBOX_PREVIEW_HOSTS","").split(",") if x.strip()}
    local_test=os.getenv("OPERLY_ENV") in {"test","development"} and os.getenv("OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER")=="1" and parsed.hostname=="127.0.0.1"
    if parsed.scheme not in ({"http","https"} if local_test else {"https"}) or not parsed.hostname or (not local_test and parsed.hostname.lower() not in allowed):raise HTTPException(502,"Preview target is not an approved runner origin")
    return value.rstrip("/")


@router.api_route("/api/custom-software/previews/{preview_id}/{path:path}",methods=["GET","POST"])
async def preview_proxy(preview_id:str,path:str,request:Request,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if path.startswith(("_runner","admin","internal")):raise HTTPException(404,"Preview route not found")
    try:preview,_=await active_preview(db,auth.tenant.id,preview_id)
    except LookupError as error:raise HTTPException(404,str(error)) from error
    body=await request.body()
    if len(body)>1_000_000:raise HTTPException(413,"Preview request is too large")
    target=_validated_preview_target(preview.target_url)+"/"+path
    headers={k:v for k,v in request.headers.items() if k.lower() in {"content-type","accept"}}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.request(request.method,target,data=body,headers=headers) as upstream:
                data=await upstream.content.read(2_000_001)
                if len(data)>2_000_000:raise HTTPException(502,"Preview response exceeded the size limit")
                safe={k:v for k,v in upstream.headers.items() if k.lower() in {"content-type","cache-control"}}
                return Response(data,status_code=upstream.status,headers=safe)
    except aiohttp.ClientError as error:raise HTTPException(502,"Preview runner is unavailable") from error


@router.delete("/api/custom-software/previews/{preview_id}")
async def terminate_preview(preview_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:preview,build=await active_preview(db,auth.tenant.id,preview_id);await stop_preview(db,preview,build,ExternalRunnerAdapter());return {"state":"cleaned"}
    except LookupError as error:raise HTTPException(404,str(error)) from error


@router.post("/api/custom-software/agentic-projects",status_code=202)
async def generate_agentic(payload:AgenticProjectInput,auth:AuthContext=Depends(get_auth_context)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can start agentic generation")
    try:return await SandboxRunner().generate(payload.prompt,auth.tenant.id,auth.user.id)
    except SandboxUnavailable as error:raise HTTPException(503,detail={"code":"sandbox_not_configured","message":str(error),"plan":generation_plan(payload.prompt)}) from error
    except SandboxFailure as error:raise HTTPException(502,detail={"code":"sandbox_failed","message":str(error)}) from error


def project_json(row):
    return {"id":row.id,"slug":row.slug,"name":row.name,"vertical":row.vertical,"architecturePack":row.architecture_pack,"planId":row.plan_id,"approvedPlanVersion":row.approved_plan_version,"version":row.version,"publicUrl":f"/generated/{row.slug}","dispatchUrl":f"/generated/{row.slug}/dispatch" if row.architecture_pack=="field_service" else f"/generated/{row.slug}/manage","artifactGraph":json.loads(row.artifact_graph_json)}


async def owned_project(db,auth,project_id):
    row=await db.get(GeneratedProject,project_id)
    if not row or row.tenant_id!=auth.tenant.id:raise HTTPException(404,"Project not found")
    return row


@router.get("/api/custom-software/projects")
async def projects(auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    from sqlalchemy import desc
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
    except DomainError as error:raise HTTPException(422,str(error)) from error


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
    except ConflictError as error:raise HTTPException(409,str(error)) from error
    except LookupError as error:raise HTTPException(404,str(error)) from error


@router.get("/generated/{slug}",response_class=HTMLResponse)
async def public_site(slug:str,db:AsyncSession=Depends(get_db)):
    try:
        project=await public_project(db,slug)
        if project.architecture_pack=="quotation":return render_quotation_public(project)
        if project.architecture_pack=="inventory":raise HTTPException(401,"Inventory workspace requires authentication")
        return render_public(project)
    except LookupError as error:raise HTTPException(404,str(error)) from error


@router.post("/api/custom-software/projects/{project_id}/visual-changes/{change_id}/rollback")
async def rollback_visual(project_id:str,change_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    if auth.role!="owner":raise HTTPException(403,"Only owners can rollback generated software")
    project=await owned_project(db,auth,project_id);change=await db.get(GeneratedProjectChangeSet,change_id)
    if not change:raise HTTPException(404,"Change set not found")
    try:return project_json(await rollback_visual_change(db,project,change))
    except ConflictError as error:raise HTTPException(409,str(error)) from error
    except LookupError as error:raise HTTPException(404,str(error)) from error


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
    except LookupError as error:raise HTTPException(404,str(error)) from error


@router.post("/api/public/service-projects/{slug}/requests")
async def submit(slug:str,payload:ServiceRequestInput,db:AsyncSession=Depends(get_db)):
    try:
        row,created=await create_request(db,await public_project(db,slug),payload)
        token=status_token(row.id)
        return {"id":row.id,"reference":row.reference,"status":row.status,"created":created,"statusUrl":f"/generated/{slug}/status/{row.reference}?token={token}"}
    except LookupError as error:raise HTTPException(404,str(error)) from error


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
    except ConflictError as error:raise HTTPException(409,str(error)) from error
    except LookupError as error:raise HTTPException(404,str(error)) from error
    except DomainError as error:raise HTTPException(422,str(error)) from error
