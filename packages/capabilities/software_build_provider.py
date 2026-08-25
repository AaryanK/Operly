"""High-level AgentRuntime software capabilities shared by AI, Studio and workflows."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import desc, select

from packages.artifacts.service import ArtifactScope, ArtifactService, artifact_json
from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.coding_harness.execution_loop import build_with_repair
from packages.coding_harness.opencode_agent import CapabilityCodingAgent
from packages.coding_harness.source_service import edit_source_for_plan
from packages.custom_software.plan_service import plan_version
from packages.custom_software.runner_adapters import ExternalRunnerAdapter
from packages.custom_software.source_bundles import SourceFile
from packages.database.custom_software_models import GeneratedSourceBundle, SoftwarePlanRecord
from packages.database.product_models import SolutionJob, SolutionRecord
from packages.database.software_project_models import SoftwareProjectRecord
from packages.model_runtime.trace_context import current_trace_metadata
from packages.software_projects import ProjectState, SoftwareProjectService, SoftwareSourceService, files_from_row
from packages.software_projects.delivery import persist_canonical_source_archive
from packages.solutions.generation_worker import queue_software_generation
from packages.solutions.service import RuntimeType, SolutionService, solution_json
from packages.tasks.delivery import capture_task_origin, delivery_target_from_origin


def _json(value:str|None)->dict[str,Any]:
    try:parsed=json.loads(value or "{}")
    except Exception:return {}
    return parsed if isinstance(parsed,dict) else {}

def _project_json(project)->dict[str,Any]:return {"id":project.id,"workspace_id":project.workspace_id,"name":project.name,"description":project.description,"state":project.state.value,"active_source_version_id":project.active_source_version_id,"active_runtime_id":project.active_runtime_id,"service_binding_ids":list(project.service_binding_ids),"metadata":dict(project.metadata)}
def _default_name(objective:str)->str:
    text=" ".join(str(objective or "").replace("\x00","").split()).strip();sentence=text.split(".",1)[0].strip() if text else "Software Project";return sentence[:80].rstrip(" ,;:-") or "Software Project"
def _active_runtime_run_id(context)->str|None:
    invocation=context.invocation if isinstance(context.invocation,dict) else {};metadata=invocation.get("metadata") if isinstance(invocation.get("metadata"),dict) else {};explicit=str(metadata.get("runtime_run_id") or "").strip();return explicit or str(current_trace_metadata().get("runtime_run_id") or "").strip() or None
def _job_evidence(job)->dict[str,Any]:return _json(job.evidence_json) if job else {}
def _job_json(job)->dict[str,Any]|None:
    if not job:return None
    evidence=_job_evidence(job);return {"id":job.id,"status":job.status,"attempt":job.attempt,"plan_id":job.plan_id,"source_version_reference":job.source_version_reference,"failure_classification":job.failure_classification,"build_id":evidence.get("buildId"),"build_state":evidence.get("buildState"),"source_bundle_id":evidence.get("sourceBundleId"),"source_version":evidence.get("sourceVersion"),"canonical_source_version_id":evidence.get("canonicalSourceVersionId"),"source_archive_artifact_id":evidence.get("sourceArchiveArtifactId"),"parent_agent_run_id":evidence.get("parentAgentRunId"),"started_at":job.started_at.isoformat() if job.started_at else None,"ended_at":job.ended_at.isoformat() if job.ended_at else None}
async def _latest_job(db,solution_id:str)->SolutionJob|None:return await db.scalar(select(SolutionJob).where(SolutionJob.solution_id==solution_id).order_by(desc(SolutionJob.attempt),desc(SolutionJob.created_at)).limit(1))
def _runner_verified(job)->bool:
    evidence=_job_evidence(job);return bool(job and job.status=="succeeded" and str(evidence.get("buildState") or "")=="preview_ready" and evidence.get("buildId") and (evidence.get("canonicalSourceVersionId") or evidence.get("sourceBundleId")))
async def _archive_from_job(db,tenant_id:str,job)->dict[str,Any]|None:
    artifact_id=str(_job_evidence(job).get("sourceArchiveArtifactId") or "").strip()
    if not artifact_id:return None
    try:row=await ArtifactService(db).get(ArtifactScope("workspace",tenant_id,tenant_id=tenant_id),artifact_id)
    except LookupError:return None
    return artifact_json(row)


def _studio_context(arguments:dict[str,Any])->dict[str,Any]:
    value=arguments.get("studio_context")
    if not isinstance(value,dict):return {}
    allowed={"route","viewport","selection","preview_url","source_version_id"}
    return {key:value[key] for key in allowed if key in value}


class SoftwareBuildProvider(BaseProvider):
    name="operly_software_build"
    capabilities=(
        CapabilityDefinition("software.build","software_build","Create a canonical SoftwareProject or initialize an explicitly selected existing project in place, then start a private durable build. Planning, coding, isolated build/test/start/health/acceptance and bounded repair are owned internally. Every project is immediately present in the Solutions Library.",{"type":"object","properties":{"objective":{"type":"string","minLength":1,"maxLength":12000},"name":{"type":"string","minLength":1,"maxLength":200},"project_id":{"type":"string","minLength":1,"maxLength":36,"description":"Optional existing SoftwareProject or legacy Studio project facade to initialize in place."},"return_source_archive":{"type":"boolean","default":True}},"required":["objective"],"additionalProperties":False},{"type":"object"},risk_level="medium",permissions=("solution:generate",),approval_policy=ApprovalPolicy.AUTO,reversible=True,plugin_id="operly.software",category="software",display_name="Build software",tags=frozenset({"software","application","codebase","source","studio","build","agent"}),semantic_operations=frozenset({"build an application","build complete software","create a working codebase","generate runnable source code","build and test software","create app source files","build software project for studio"})),
        CapabilityDefinition("software.edit","software_edit","Edit an existing canonical SoftwareProject using the same coding worker used by Operly AI and Studio. Studio visual/DOM context is observation only; authority remains project/capability scoped.",{"type":"object","properties":{"project_id":{"type":"string","minLength":1,"maxLength":36},"instruction":{"type":"string","minLength":1,"maxLength":12000},"studio_context":{"type":"object"}},"required":["project_id","instruction"],"additionalProperties":False},{"type":"object"},risk_level="medium",permissions=("solution:generate",),approval_policy=ApprovalPolicy.AUTO,reversible=True,plugin_id="operly.software",category="software",display_name="Edit software",tags=frozenset({"software","edit","studio","visual","source"}),semantic_operations=frozenset({"edit software project","change existing application","update studio project","fix website design","modify app source","edit selected studio element"})),
        CapabilityDefinition("software.build.status","software_build_status","Inspect durable software progress and verified source/build/test/start/health/acceptance evidence for one SoftwareProject.",{"type":"object","properties":{"project_id":{"type":"string","minLength":1,"maxLength":36}},"required":["project_id"],"additionalProperties":False},{"type":"object"},risk_level="read_only",permissions=("solution:read",),approval_policy=ApprovalPolicy.AUTO,plugin_id="operly.software",category="software",display_name="Inspect software build",tags=frozenset({"software","build","status","verification","studio"}),semantic_operations=frozenset({"check software build","inspect app build progress","verify generated application"})),
        CapabilityDefinition("software.source.export","software_source_export","Export the current canonical immutable source version as a scoped ZIP delivery artifact. The archive is projection-only and never becomes source truth.",{"type":"object","properties":{"project_id":{"type":"string","minLength":1,"maxLength":36},"filename":{"type":"string","minLength":1,"maxLength":255}},"required":["project_id"],"additionalProperties":False},{"type":"object"},risk_level="low",permissions=("solution:read","files:process"),approval_policy=ApprovalPolicy.AUTO,reversible=True,plugin_id="operly.software",category="software",display_name="Export software source",tags=frozenset({"software","source","zip","download","artifact","codebase"}),semantic_operations=frozenset({"download source code","export codebase","create source zip","give me project files"})),
    )

    def __init__(self)->None:self.projects=SoftwareProjectService();self.sources=SoftwareSourceService();self.solutions=SolutionService()

    async def _solution(self,context,project_id:str):
        project=await self.projects.get(context.db,context.tenant_id,project_id)
        solution=await context.db.scalar(select(SolutionRecord).where(SolutionRecord.tenant_id==context.tenant_id,SolutionRecord.runtime_type==RuntimeType.SOFTWARE_PROJECT,SolutionRecord.runtime_reference==project.id))
        if solution is None:
            target=await self.projects.legacy_target(context.db,context.tenant_id,project.id)
            if target:solution=await context.db.scalar(select(SolutionRecord).where(SolutionRecord.tenant_id==context.tenant_id,SolutionRecord.runtime_type==target[0],SolutionRecord.runtime_reference==target[1]))
        return project,solution

    async def _status_payload(self,context,project_id:str)->dict[str,Any]:
        await self.solutions.sync(context.db,context.tenant_id);project,solution=await self._solution(context,project_id)
        if solution is None:return {"project":_project_json(project),"project_id":project.id,"build_state":project.state.value,"build_success":False,"preview_available":project.state.value=="preview_ready","reason":"project_has_no_solution_build_lifecycle"}
        job=await _latest_job(context.db,solution.id);verified=_runner_verified(job);archive=await _archive_from_job(context.db,context.tenant_id,job);evidence=_job_evidence(job)
        payload={"project":_project_json(project),"project_id":project.id,"solution":solution_json(solution),"solution_id":solution.id,"job":_job_json(job),"job_id":job.id if job else None,"lifecycle_status":str(solution.lifecycle_status),"build_state":evidence.get("buildState") or str(solution.lifecycle_status),"source_bundle_id":evidence.get("sourceBundleId"),"source_version":evidence.get("sourceVersion"),"canonical_source_version_id":evidence.get("canonicalSourceVersionId") or project.active_source_version_id,"build_id":evidence.get("buildId"),"preview_available":solution.preview_state=="ready","preview_url":solution_json(solution).get("preview",{}).get("url"),"production_state":solution.production_state,"production_url":solution.production_url,"private_preview_only":solution.production_state!="live","publication_performed":solution.production_state=="live","source_archive":archive,"source_archive_artifact_id":archive.get("artifact_id") if archive else None,"build_success":verified,"test_success":verified,"tests_passed":verified,"process_start_success":verified,"process_started":verified,"health_check_success":verified,"health_passed":verified,"acceptance_check_success":verified,"acceptance_passed":verified}
        if job and job.failure_classification:payload["failure_classification"]=job.failure_classification;payload["failure_evidence"]=evidence
        return payload

    async def execute(self,context,capability_name,arguments):
        if capability_name=="software.build":
            if not context.actor_id:return CapabilityResult(False,False,{"reason":"authenticated_actor_required"})
            objective=" ".join(str(arguments.get("objective") or "").replace("\x00","").split()).strip()[:12000]
            if not objective:return CapabilityResult(False,False,{"reason":"objective_required"})
            name=" ".join(str(arguments.get("name") or _default_name(objective)).replace("\x00","").split()).strip()[:200];requested_project_id=str(arguments.get("project_id") or "").strip();return_archive=bool(arguments.get("return_source_archive",True));parent_run=_active_runtime_run_id(context);origin=await capture_task_origin(context);target=delivery_target_from_origin(origin);target["artifact_scope"]="workspace";target["artifact_tenant_id"]=str(context.tenant_id or "")
            project_reused=False
            if requested_project_id:
                try:project=await self.projects.get(context.db,context.tenant_id,requested_project_id)
                except LookupError:return CapabilityResult(False,False,{"reason":"software_project_not_found","project_id":requested_project_id})
                current=await self.sources.latest(context.db,context.tenant_id,project.id)
                if current is not None:return CapabilityResult(False,False,{"reason":"software_project_already_initialized","project_id":project.id,"source_version_id":current.id,"source_version":current.source_version,"hint":"Use software.edit for an existing source version instead of rebuilding the project."})
                record=await self.projects.record(context.db,context.tenant_id,project.id)
                if record is None:return CapabilityResult(False,False,{"reason":"software_project_not_persisted","project_id":project.id})
                project_reused=True
            else:
                project=await self.projects.create(context.db,workspace_id=context.tenant_id,user_id=context.actor_id,name=name,description=objective,metadata={"created_via":"software.build","source_authority":"software_source_versions"});record=await self.projects.record(context.db,context.tenant_id,project.id)
                if record is None:return CapabilityResult(False,False,{"reason":"software_project_not_persisted","project_id":project.id})
            build_request={"requestedBy":context.actor_id,"returnSourceArchive":return_archive,"privatePreviewOnly":True,"publishAuthorized":False,"parentAgentRunId":parent_run,"origin":origin,"deliveryTarget":target,"existingProject":project_reused,"canonicalSoftwareProjectId":project.id}
            solution=await self.solutions.create_software_solution(context.db,tenant_id=context.tenant_id,user_id=context.actor_id,project=record,objective=objective,context={"softwareBuildRequest":build_request});solution,job=await queue_software_generation(context.db,row=solution,user_id=context.actor_id)
            evidence=_job_evidence(job);evidence.update({"parentAgentRunId":parent_run,"returnSourceArchive":return_archive,"deliveryTarget":target,"existingProject":project_reused,"canonicalSoftwareProjectId":project.id});job.evidence_json=json.dumps(evidence,ensure_ascii=False,sort_keys=True,default=str)
            payload={"project":_project_json(project),"project_id":project.id,"project_reused":project_reused,"solution":solution_json(solution),"solution_id":solution.id,"classification":{"runtime_type":"software_project","canonical":True},"job":_job_json(job),"job_id":job.id,"job_accepted":True,"build_state":job.status,"build_success":False,"source_archive_requested":return_archive,"private_preview_only":True,"publication_performed":False,"deployment_performed":False,"deferred":True,"continuation_kind":"software_build","parent_agent_run_id":parent_run}
            return CapabilityResult(True,True,payload,project.id)

        if capability_name=="software.edit":
            if not context.actor_id:return CapabilityResult(False,False,{"reason":"authenticated_actor_required"})
            project,solution=await self._solution(context,str(arguments.get("project_id") or ""));instruction=" ".join(str(arguments.get("instruction") or "").replace("\x00","").split()).strip()[:12000]
            if not instruction:return CapabilityResult(False,False,{"reason":"instruction_required"})
            current=await self.sources.latest(context.db,context.tenant_id,project.id)
            if current is None:return CapabilityResult(False,False,{"reason":"software_source_missing","project_id":project.id})
            studio_context=_studio_context(arguments);job=await _latest_job(context.db,solution.id) if solution else None
            if job and job.plan_id:
                plan_row=await context.db.get(SoftwarePlanRecord,job.plan_id)
                if plan_row is None or not plan_row.approved_version:return CapabilityResult(False,False,{"reason":"software_plan_missing"})
                _,plan=await plan_version(context.db,plan_row,plan_row.approved_version)
                adapter=await context.db.scalar(select(GeneratedSourceBundle).where(GeneratedSourceBundle.tenant_id==context.tenant_id,GeneratedSourceBundle.plan_id==plan_row.id,GeneratedSourceBundle.plan_version==plan_row.approved_version).order_by(desc(GeneratedSourceBundle.source_version)).limit(1))
                if adapter is None:return CapabilityResult(False,False,{"reason":"runner_source_adapter_missing"})
                edited,result=await edit_source_for_plan(context.db,context.tenant_id,context.actor_id,plan_row,plan,adapter,instruction,edit_kind="software_edit",context=studio_context);await context.db.flush();build,final_source,repairs=await build_with_repair(context.db,context.tenant_id,context.actor_id,plan_row,plan,f"solution:{solution.id}:generated-build:{int(job.attempt or 1)}-edit-{_active_runtime_run_id(context) or 'run'}",adapter=ExternalRunnerAdapter())
                if build.state!="preview_ready":return CapabilityResult(False,True,{"reason":"software_edit_build_failed","project_id":project.id,"build_id":build.id,"build_state":build.state,"failure_classification":build.failure_classification,"changed_paths":getattr(result,"changed_paths",[])})
                canonical=await self.sources.import_generated(context.db,tenant_id=context.tenant_id,project_id=project.id,source=final_source,originating_run_id=_active_runtime_run_id(context));await self.projects.set_execution_state(context.db,workspace_id=context.tenant_id,project_id=project.id,source_version_id=canonical.id,runtime_id=canonical.runtime_profile,state=ProjectState.PREVIEW_READY)
                if solution:solution.current_version_reference=canonical.id;solution.lifecycle_status="preview_ready";solution.preview_state="ready";solution.preview_url="/api/solutions/{solution_id}/preview"
                return CapabilityResult(True,True,{"project_id":project.id,"canonical_source_version_id":canonical.id,"source_version":canonical.source_version,"build_id":build.id,"build_state":build.state,"build_success":True,"tests_passed":True,"process_started":True,"health_passed":True,"acceptance_passed":True,"changed_paths":getattr(result,"changed_paths",[]),"repair_count":len(repairs),"studio_observation_used":bool(studio_context)},canonical.id)
            # Migration path for an existing legacy static Studio project. It uses the
            # same generic coding worker, not source_agent/StudioAI, and writes only
            # canonical source. Static preview remains non-executing.
            files=[SourceFile(path,content.encode("utf-8"),"canonical_software_source") for path,content in files_from_row(current).items()]
            specification=(f"Existing SoftwareProject: {project.name}\nPurpose: {project.description}\nPreserve unrelated behavior and implement the requested edit. The source must remain complete, responsive, accessible, and tested.")[:80000]
            result=await CapabilityCodingAgent().edit(specification,files,instruction,context=studio_context);mapped={item.path:item.content.decode("utf-8") for item in result.files};canonical=await self.sources.persist(context.db,tenant_id=context.tenant_id,project_id=project.id,user_id=context.actor_id,files=mapped,runtime_profile=current.runtime_profile or "static-web-js",provenance={"sourceOperation":"software_edit","modelProvider":result.model_provider,"modelId":result.model_id,"changedPaths":result.changed_paths,"verificationIntent":result.verification,"studioObservationUsed":bool(studio_context)},change_summary=result.summary,originating_run_id=_active_runtime_run_id(context),parent_source_id=current.id);await self.projects.set_execution_state(context.db,workspace_id=context.tenant_id,project_id=project.id,source_version_id=canonical.id,runtime_id=canonical.runtime_profile,state=ProjectState.PREVIEW_READY)
            return CapabilityResult(True,True,{"project_id":project.id,"canonical_source_version_id":canonical.id,"source_version":canonical.source_version,"build_state":"source_verified_static_preview","build_success":False,"changed_paths":result.changed_paths,"verification":result.verification,"studio_observation_used":bool(studio_context),"legacy_static_adopted":True},canonical.id)

        if capability_name=="software.build.status":
            try:payload=await self._status_payload(context,str(arguments["project_id"]))
            except LookupError as error:return CapabilityResult(False,False,{"reason":str(error)})
            return CapabilityResult(True,False,payload,payload.get("project_id"))

        if capability_name=="software.source.export":
            if not context.actor_id:return CapabilityResult(False,False,{"reason":"authenticated_actor_required"})
            try:project=await self.projects.get(context.db,context.tenant_id,str(arguments["project_id"]));source=await self.sources.latest(context.db,context.tenant_id,project.id)
            except LookupError as error:return CapabilityResult(False,False,{"reason":str(error)})
            if source is None:return CapabilityResult(False,False,{"reason":"software_source_not_available_yet","project_id":project.id})
            filename=str(arguments.get("filename") or f"{project.name}-source-v{source.source_version}.zip");artifact=await persist_canonical_source_archive(context.db,tenant_id=context.tenant_id,created_by=context.actor_id,source=source,filename=filename,run_id=_active_runtime_run_id(context));project,solution=await self._solution(context,project.id);job=await _latest_job(context.db,solution.id) if solution else None
            if job:
                evidence=_job_evidence(job);evidence.update({"sourceArchiveArtifactId":artifact["artifact_id"],"sourceArchiveFilename":artifact["filename"],"sourceArchiveSha256":artifact["sha256"]});job.evidence_json=json.dumps(evidence,ensure_ascii=False)
            return CapabilityResult(True,True,{"project_id":project.id,"canonical_source_version_id":source.id,"source_version":source.source_version,"artifact_id":artifact["artifact_id"],"artifact_ids":[artifact["artifact_id"]],"artifacts":[artifact],"artifact_kind":"software_source_archive","source_archive":artifact,"persisted":True,"projection_only":True,"executed":False},artifact["artifact_id"])
        return CapabilityResult(False,False,{"reason":"unsupported_software_capability"})

    async def verify(self,context,capability_name,arguments,result):
        if not result.success:return CapabilityResult(False,result.changed,result.evidence,result.external_reference)
        if capability_name=="software.build":
            try:project=await self.projects.get(context.db,context.tenant_id,str(result.external_reference or ""))
            except LookupError:return CapabilityResult(False,result.changed,{"reason":"software_project_not_persisted"})
            solution_id=str(result.evidence.get("solution_id") or "");solution=await context.db.scalar(select(SolutionRecord).where(SolutionRecord.id==solution_id,SolutionRecord.tenant_id==context.tenant_id, SolutionRecord.runtime_type==RuntimeType.SOFTWARE_PROJECT))
            if solution is None:return CapabilityResult(False,result.changed,{"reason":"software_solution_not_persisted"})
            return CapabilityResult(True,result.changed,{"persisted":True,"project_id":project.id,"solution_library":True,**result.evidence},project.id)
        if capability_name=="software.edit":return CapabilityResult(True,result.changed,{"verified_operation":True,**result.evidence},result.external_reference)
        if capability_name=="software.build.status":return CapabilityResult(True,False,{"observed":True,**result.evidence},result.external_reference)
        if capability_name=="software.source.export":
            artifact_id=str(result.evidence.get("artifact_id") or "")
            if not artifact_id:return CapabilityResult(False,result.changed,{"reason":"source_archive_artifact_missing"})
            try:row=await ArtifactService(context.db).get(ArtifactScope("workspace",context.tenant_id,tenant_id=context.tenant_id),artifact_id)
            except LookupError:return CapabilityResult(False,result.changed,{"reason":"source_archive_not_persisted"})
            return CapabilityResult(True,result.changed,{**result.evidence,"artifact_id":row.id,"persisted":True,"sha256":row.sha256,"filename":row.filename},row.id)
        return CapabilityResult(False,result.changed,{"reason":"unsupported_software_capability"})

__all__=["SoftwareBuildProvider"]
