"""Durable Postgres-backed canonical SoftwareProject generation worker.

GeneratedSourceBundle and SoftwarePlanRecord remain internal execution adapters for
planning/runner mechanics. Product identity and source authority are exclusively
SoftwareProject and SoftwareSourceVersionRecord.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import and_, desc, or_, select

from packages.coding_harness.execution_loop import build_with_repair
from packages.custom_software.compiler_planning import PLANNING_ENGINE_VERSION as PLANNING_ENGINE
from packages.custom_software.live_planning import PlanningBlocked, PlanningMode, PlannerUnavailable
from packages.custom_software.model_planning_client import planning_mode
from packages.custom_software.plan_service import _persist_first_version, _run_live_plan, _store_clarification, approve, plan_version
from packages.custom_software.planner import build_software_plan
from packages.custom_software.planning_orchestrator import PlanningNeedsUserInput
from packages.custom_software.runner_adapters import ExternalRunnerAdapter
from packages.custom_software.schema import SoftwarePlan
from packages.database.custom_software_models import SoftwarePlanRecord, SoftwarePlanVersion
from packages.database.db import SessionFactory, init_db
from packages.database.product_models import SolutionJob, SolutionRecord
from packages.database.software_project_models import SoftwareProjectRecord
from packages.software_projects import SoftwareSourceService
from packages.solutions.completion import finalize_software_job
from packages.solutions.service import LifecycleStatus, RuntimeType

SOFTWARE_JOB_TYPE="software_generation"
SUPPORTED_JOB_TYPES=(SOFTWARE_JOB_TYPE,)


def worker_enabled()->bool:return os.getenv("OPERLY_SOLUTION_WORKER_ENABLED","0").strip().lower() in {"1","true","yes","on"}
def _lease_seconds()->int:
    try:return max(30,min(int(os.getenv("OPERLY_SOLUTION_WORKER_LEASE_SECONDS","120")),900))
    except ValueError:return 120
def _heartbeat_seconds()->int:return max(10,min(_lease_seconds()//3,60))
def _poll_seconds()->float:
    try:return max(.25,min(float(os.getenv("OPERLY_SOLUTION_WORKER_POLL_SECONDS","2")),30.0))
    except ValueError:return 2.0
def worker_identity()->str:
    configured=os.getenv("OPERLY_SOLUTION_WORKER_ID","").strip();return configured[:160] if configured else f"{socket.gethostname()}:{uuid4().hex[:12]}"[:160]

def _context(row:SolutionRecord)->dict[str,Any]:
    try:value=json.loads(row.context_json or "{}")
    except Exception:return {}
    return value if isinstance(value,dict) else {}
def _evidence(job:SolutionJob)->dict[str,Any]:
    try:value=json.loads(job.evidence_json or "{}")
    except Exception:return {}
    return value if isinstance(value,dict) else {}
def _logs(job:SolutionJob)->list[dict[str,Any]]:
    try:value=json.loads(job.log_json or "[]")
    except Exception:return []
    return value if isinstance(value,list) else []
def _append_log(job:SolutionJob,stage:str,status:str,detail:str|None=None)->None:
    rows=_logs(job);item={"at":datetime.utcnow().isoformat()+"Z","stage":stage,"status":status}
    if detail:item["detail"]=" ".join(str(detail).split())[:1000]
    rows.append(item);job.log_json=json.dumps(rows[-200:],ensure_ascii=False)

def _planning_prompt(name:str,objective:str,context:dict[str,Any])->str:
    payload={"name":name,"objective":objective,"solutionManifest":context.get("solutionManifest",{}),"implementationResolution":context.get("implementationResolution",{}),"constraints":["Implement every mandatory behavior as executable software, not a mock or brochure.","Use Operly capability bindings for trusted data, identity, secrets, permissions and external services.","Generate acceptance tests for critical state transitions and user interactions.","The first preview must remain private; creation does not authorize publishing or external side effects."]}
    return (f"Build the Solution named {name!r}.\n\nOwner objective:\n{objective}\n\nOPERLY SOLUTION CONTRACT:\n"+json.dumps(payload,ensure_ascii=False,sort_keys=True))[:20000]
def _planning_input_digest(name:str,objective:str,context:dict[str,Any])->str:
    payload={"planningEngine":PLANNING_ENGINE,"name":" ".join(str(name or "").split()),"objective":" ".join(str(objective or "").split()),"solutionManifest":context.get("solutionManifest",{}),"implementationResolution":context.get("implementationResolution",{})}
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()

async def _reusable_approved_plan(db,row:SolutionRecord,planning_input_digest:str)->SoftwarePlanRecord|None:
    prior=(await db.scalars(select(SolutionJob).where(SolutionJob.tenant_id==row.tenant_id,SolutionJob.solution_id==row.id,SolutionJob.job_type.in_(("initial_generation",SOFTWARE_JOB_TYPE)),SolutionJob.plan_id.is_not(None)).order_by(desc(SolutionJob.attempt),desc(SolutionJob.created_at)).limit(20))).all()
    for item in prior:
        evidence=_evidence(item)
        if evidence.get("planningEngine")!=PLANNING_ENGINE or evidence.get("planningInputDigest")!=planning_input_digest:continue
        plan=await db.get(SoftwarePlanRecord,item.plan_id)
        if plan is not None and plan.tenant_id==row.tenant_id and plan.approved_version is not None:return plan
    return None

async def _next_attempt(db,tenant_id:str,solution_id:str)->int:
    previous=await db.scalar(select(SolutionJob).where(SolutionJob.tenant_id==tenant_id,SolutionJob.solution_id==solution_id,SolutionJob.job_type.in_(("initial_generation",SOFTWARE_JOB_TYPE))).order_by(desc(SolutionJob.attempt)).limit(1));return int(previous.attempt)+1 if previous else 1

async def _queue_generation(db,*,row:SolutionRecord,user_id:str)->tuple[SolutionRecord,SolutionJob]:
    if RuntimeType(row.runtime_type)!=RuntimeType.SOFTWARE_PROJECT:raise ValueError("Software generation requires a SoftwareProject-backed Solution")
    active=await db.scalar(select(SolutionJob).where(SolutionJob.tenant_id==row.tenant_id,SolutionJob.solution_id==row.id,SolutionJob.job_type==SOFTWARE_JOB_TYPE,SolutionJob.status.in_(("queued","running"))).order_by(desc(SolutionJob.attempt)).limit(1))
    if active:return row,active
    attempt=await _next_attempt(db,row.tenant_id,row.id);context=_context(row);owner=context.get("ownerIntent") if isinstance(context.get("ownerIntent"),dict) else {};objective=" ".join(str(owner.get("objective") or row.description or "").split()).strip()[:8000];digest=_planning_input_digest(row.name,objective,context);reusable=await _reusable_approved_plan(db,row,digest);source_reference=f"software-plan:{reusable.id}:{reusable.approved_version}" if reusable else f"owner-intent:{row.id}"
    evidence={"objective":objective,"createdBy":user_id,"implementationResolution":context.get("implementationResolution",{}),"planningEngine":PLANNING_ENGINE,"planningInputDigest":digest}
    request=context.get("softwareBuildRequest") if isinstance(context.get("softwareBuildRequest"),dict) else {}
    for key,target in (("parentAgentRunId","parentAgentRunId"),("returnSourceArchive","returnSourceArchive"),("deliveryTarget","deliveryTarget")):
        if request.get(key) is not None:evidence[target]=request.get(key)
    if reusable:evidence.update({"reusedSoftwarePlanId":reusable.id,"reusedSoftwarePlanVersion":reusable.approved_version})
    job=SolutionJob(tenant_id=row.tenant_id,solution_id=row.id,source_version_reference=source_reference,job_type=SOFTWARE_JOB_TYPE,status="queued",attempt=attempt,created_by=user_id,plan_id=reusable.id if reusable else None,log_json="[]",evidence_json=json.dumps(evidence,ensure_ascii=False),idempotency_key=f"solution:{row.id}:software-build:{attempt}")
    _append_log(job,"queue","queued","SoftwareProject Solution queued for durable worker execution")
    if reusable:_append_log(job,"planning","reused",f"Reusing approved SoftwarePlan v{reusable.approved_version}; retry resumes at source generation")
    db.add(job);await db.flush();initial={"status":"queued","stage":"source_generation" if reusable else "planning","jobId":job.id,"attempt":attempt}
    if reusable:initial.update({"softwarePlanId":reusable.id,"softwarePlanVersion":reusable.approved_version,"resumedFromCheckpoint":"planning"})
    context["initialGeneration"]=initial;row.lifecycle_status=LifecycleStatus.BUILDING;row.current_version_reference=None;row.preview_state="unavailable";row.preview_url=None;row.context_json=json.dumps(context,ensure_ascii=False,sort_keys=True);await db.flush();return row,job

async def queue_software_generation(db,*,row:SolutionRecord,user_id:str):return await _queue_generation(db,row=row,user_id=user_id)

async def _create_plan_record(db,job:SolutionJob,row:SolutionRecord,user_id:str)->SoftwarePlanRecord:
    context=_context(row);owner=context.get("ownerIntent") if isinstance(context.get("ownerIntent"),dict) else {};objective=" ".join(str(owner.get("objective") or row.description or "").split()).strip()[:8000];plan=SoftwarePlanRecord(tenant_id=row.tenant_id,prompt=_planning_prompt(row.name,objective,context),created_by=user_id,status="planning");db.add(plan);await db.flush();job.plan_id=plan.id;job.source_version_reference=f"software-plan:{plan.id}:pending";evidence=_evidence(job);evidence["softwarePlanId"]=plan.id;job.evidence_json=json.dumps(evidence,ensure_ascii=False);_append_log(job,"planning","running",f"SoftwarePlan {plan.id} persisted before model planning");context["initialGeneration"]={"status":"running","stage":"planning","jobId":job.id,"attempt":job.attempt,"softwarePlanId":plan.id};row.context_json=json.dumps(context,ensure_ascii=False,sort_keys=True);await db.commit();await db.refresh(job);await db.refresh(plan);return plan

def _deterministic_plan(prompt:str)->SoftwarePlan:
    planned=build_software_plan(prompt);data=planned.model_dump();data["planningMode"]="deterministic_test";data["planningMetrics"]["planningMode"]="deterministic_test"
    for item in data["requirementLedger"]:item["planningMode"]="deterministic_test"
    for item in data["planTree"]:item["planningMode"]="deterministic_test"
    return SoftwarePlan.model_validate(data)

async def _ensure_plan(db,job:SolutionJob,row:SolutionRecord,user_id:str):
    plan_row=await db.get(SoftwarePlanRecord,job.plan_id) if job.plan_id else None
    if plan_row is None:plan_row=await _create_plan_record(db,job,row,user_id)
    if plan_row.approved_version:
        _,plan=await plan_version(db,plan_row,plan_row.approved_version);return plan_row,plan
    existing=await db.scalar(select(SoftwarePlanVersion).where(SoftwarePlanVersion.plan_id==plan_row.id,SoftwarePlanVersion.tenant_id==plan_row.tenant_id,SoftwarePlanVersion.version==plan_row.current_version))
    if existing:plan=SoftwarePlan.model_validate_json(existing.plan_json)
    else:
        mode=planning_mode()
        if mode==PlanningMode.UNAVAILABLE:raise PlannerUnavailable("planner_unavailable")
        if mode==PlanningMode.LIVE_LLM:
            try:plan=await _run_live_plan(db,plan_row,plan_row.tenant_id,plan_row.prompt)
            except PlanningNeedsUserInput as error:
                await _store_clarification(db,plan_row,error.questions);error.plan_id=plan_row.id;raise
            except ValidationError as error:
                plan_row.status="planning_blocked";await db.commit();details="; ".join(f"{'.'.join(str(part) for part in item.get('loc',[]))}: {item.get('msg','invalid')}" for item in error.errors()[:8]);raise PlanningBlocked(f"live plan projection failed schema validation: {details}") from error
            except Exception:plan_row.status="planning_blocked";await db.commit();raise
        else:plan=_deterministic_plan(plan_row.prompt)
        await _persist_first_version(db,plan_row,user_id,plan)
    if not plan_row.approved_version:await approve(db,plan_row,plan_row.current_version)
    await db.refresh(plan_row);job.plan_id=plan_row.id;job.source_version_reference=f"software-plan:{plan_row.id}:{plan_row.approved_version}";evidence=_evidence(job);evidence.update({"softwarePlanId":plan_row.id,"softwarePlanVersion":plan_row.approved_version});job.evidence_json=json.dumps(evidence,ensure_ascii=False);_append_log(job,"planning","succeeded",f"SoftwarePlan v{plan_row.approved_version} validated and approved");await db.commit();return plan_row,plan

async def _mark_failed(db,job:SolutionJob,row:SolutionRecord,stage:str,error:Exception)->None:
    generic=" ".join(str(error).split())[:1000] or type(error).__name__;context=_context(row);previous=context.get("initialGeneration") if isinstance(context.get("initialGeneration"),dict) else {};safe=" ".join(str(previous.get("failureMessage") or "").split())[:1000] or generic;_append_log(job,stage,"failed",safe);initial=dict(previous);initial.update({"status":"retryable","stage":stage,"error":safe,"jobId":job.id,"attempt":job.attempt});
    if job.plan_id:initial["softwarePlanId"]=job.plan_id
    context["initialGeneration"]=initial;row.lifecycle_status=LifecycleStatus.FAILED;row.current_version_reference=None;row.preview_state="unavailable";row.preview_url=None;row.context_json=json.dumps(context,ensure_ascii=False,sort_keys=True);job.status="failed";job.ended_at=datetime.utcnow();job.failure_classification=str(initial.get("failureClassification") or type(error).__name__)[:80];job.locked_by=None;job.lease_expires_at=None;job.heartbeat_at=None;evidence=_evidence(job);evidence.update({"failedStage":stage,"error":safe})
    for key in ("failureClassification","failureMessage","buildId","buildState","runnerEventState","runnerExitCode","repairNumber","runnerAttempt"):
        if initial.get(key) is not None:evidence[key]=initial.get(key)
    job.evidence_json=json.dumps(evidence,ensure_ascii=False)
    project=await db.get(SoftwareProjectRecord,row.runtime_reference)
    if project and project.tenant_id==row.tenant_id:project.state="failed"
    await db.commit()

async def process_generation_job(db,job:SolutionJob)->None:
    row=await db.get(SolutionRecord,job.solution_id)
    if row is None or row.tenant_id!=job.tenant_id:raise LookupError("Software Solution job lost its Solution record")
    if RuntimeType(row.runtime_type)!=RuntimeType.SOFTWARE_PROJECT:raise ValueError("Software generation worker accepts only SoftwareProject Solutions")
    canonical_project=await db.get(SoftwareProjectRecord,row.runtime_reference)
    if canonical_project is None or canonical_project.tenant_id!=row.tenant_id:raise LookupError("SoftwareProject runtime is missing")
    user_id=job.created_by or str(_evidence(job).get("createdBy") or "")
    if not user_id:raise ValueError("Software Solution job is missing its creating principal")
    stage="planning";final_source=None
    try:
        plan_row,plan=await _ensure_plan(db,job,row,user_id)
        context=_context(row);context["softwarePlan"]={"id":plan_row.id,"version":plan_row.approved_version,"status":plan_row.status};context["initialGeneration"]={"status":"running","stage":"source_generation","jobId":job.id,"attempt":job.attempt,"softwarePlanId":plan_row.id,"softwarePlanVersion":plan_row.approved_version};row.context_json=json.dumps(context,ensure_ascii=False,sort_keys=True);job.status="running";_append_log(job,"source_generation","running","Generating executable source from the approved requirement ledger");await db.commit();stage="source_generation";last_progress=None
        async def generation_progress(next_stage:str,status:str,payload:dict[str,Any])->None:
            nonlocal stage,last_progress
            clean_stage=str(next_stage or "source_generation")[:80];clean_status=str(status or "running")[:40];stage=clean_stage;current=_context(row);previous=current.get("initialGeneration") if isinstance(current.get("initialGeneration"),dict) else {};initial=dict(previous);initial.update({"status":"running","stage":clean_stage,"stageStatus":clean_status,"jobId":job.id,"attempt":job.attempt,"softwarePlanId":plan_row.id,"softwarePlanVersion":plan_row.approved_version})
            for source_key,target_key in (("buildId","buildId"),("sourceBundleId","sourceBundleId"),("sourceVersion","sourceVersion"),("repairNumber","repairNumber"),("classification","failureClassification")):
                if payload.get(source_key) is not None:initial[target_key]=payload.get(source_key)
            if clean_status=="failed":
                for source_key,target_key in (("message","failureMessage"),("buildState","buildState"),("runnerEventState","runnerEventState"),("runnerExitCode","runnerExitCode"),("attempt","runnerAttempt")):
                    if payload.get(source_key) is not None:initial[target_key]=payload.get(source_key)
            current["initialGeneration"]=initial;row.context_json=json.dumps(current,ensure_ascii=False,sort_keys=True);signature=(clean_stage,clean_status)
            if signature!=last_progress:_append_log(job,clean_stage,clean_status,str(payload.get("message") or payload.get("classification") or payload.get("state") or payload.get("to")) if (payload.get("message") or payload.get("classification") or payload.get("state") or payload.get("to")) else None);last_progress=signature
            await db.commit()
        build,source,repairs=await build_with_repair(db,row.tenant_id,user_id,plan_row,plan,job.idempotency_key,adapter=ExternalRunnerAdapter(),progress_callback=generation_progress);final_source=source;job.source_version_reference=str(source.source_version)
        if build.state!="preview_ready":raise RuntimeError(f"Generated build did not reach preview_ready: {build.failure_classification or build.state or 'software_build_failed'}")
        provenance=json.loads(source.provenance_json or "{}") if getattr(source,"provenance_json",None) else {};provenance.update({"canonicalSoftwareProjectId":canonical_project.id,"runnerAdapter":True});source.provenance_json=json.dumps(provenance,ensure_ascii=False,sort_keys=True)
        canonical_source=await SoftwareSourceService().import_generated(db,tenant_id=row.tenant_id,project_id=canonical_project.id,source=source,originating_run_id=str(_evidence(job).get("parentAgentRunId") or "") or None);canonical_project.active_source_version_id=canonical_source.id;canonical_project.active_runtime_id=canonical_source.runtime_profile;canonical_project.state="preview_ready";job.source_version_reference=canonical_source.id
        stage="preview_readiness";_append_log(job,"build","succeeded",f"Isolated build {build.id} completed");_append_log(job,"acceptance_test","succeeded","Build, tests, health and acceptance checks passed");_append_log(job,stage,"succeeded","Verified isolated preview is active");context=_context(row);context["initialGeneration"]={"status":"applied","stage":stage,"jobId":job.id,"attempt":job.attempt,"softwarePlanId":plan_row.id,"softwarePlanVersion":plan_row.approved_version,"sourceBundleId":source.id,"sourceVersion":source.source_version,"canonicalSourceVersionId":canonical_source.id,"buildId":build.id,"repairCount":len(repairs)};row.lifecycle_status=LifecycleStatus.PREVIEW_READY;row.current_version_reference=canonical_source.id;row.preview_state="ready";row.preview_url="/api/solutions/{solution_id}/preview";row.context_json=json.dumps(context,ensure_ascii=False,sort_keys=True);job.status="succeeded";job.ended_at=datetime.utcnow();job.failure_classification=None;job.locked_by=None;job.lease_expires_at=None;job.heartbeat_at=None;evidence=_evidence(job);evidence.update({"softwarePlanId":plan_row.id,"softwarePlanVersion":plan_row.approved_version,"sourceBundleId":source.id,"sourceVersion":source.source_version,"canonicalSourceVersionId":canonical_source.id,"buildId":build.id,"buildState":build.state,"repairs":repairs});job.evidence_json=json.dumps(evidence,ensure_ascii=False);await db.commit();await finalize_software_job(db,job=job,solution=row,generated_source=source)
    except Exception as error:
        await _mark_failed(db,job,row,stage,error)
        await finalize_software_job(db,job=job,solution=row,generated_source=final_source)

async def claim_next_generation_job(worker_id:str)->str|None:
    now=datetime.utcnow();lease_until=now+timedelta(seconds=_lease_seconds())
    async with SessionFactory() as db:
        statement=select(SolutionJob).where(SolutionJob.job_type==SOFTWARE_JOB_TYPE,SolutionJob.cancellation_requested.is_(False),or_(SolutionJob.status=="queued",and_(SolutionJob.status=="running",SolutionJob.lease_expires_at.is_not(None),SolutionJob.lease_expires_at<now))).order_by(SolutionJob.queued_at,SolutionJob.created_at).limit(1).with_for_update(skip_locked=True);job=await db.scalar(statement)
        if job is None:return None
        reclaimed=job.status=="running";job.status="running";job.started_at=job.started_at or now;job.locked_by=worker_id;job.heartbeat_at=now;job.lease_expires_at=lease_until;_append_log(job,"worker_lease","reclaimed" if reclaimed else "claimed","Expired worker lease reclaimed after interruption" if reclaimed else f"Claimed by {worker_id}");await db.commit();return job.id

async def _heartbeat(job_id:str,worker_id:str,stop:asyncio.Event)->None:
    while not stop.is_set():
        try:await asyncio.wait_for(stop.wait(),timeout=_heartbeat_seconds());return
        except asyncio.TimeoutError:pass
        async with SessionFactory() as db:
            job=await db.get(SolutionJob,job_id)
            if job is None or job.status!="running" or job.locked_by!=worker_id:return
            now=datetime.utcnow();job.heartbeat_at=now;job.lease_expires_at=now+timedelta(seconds=_lease_seconds());await db.commit()

async def work_once(worker_id:str)->bool:
    job_id=await claim_next_generation_job(worker_id)
    if not job_id:return False
    stop=asyncio.Event();heartbeat=asyncio.create_task(_heartbeat(job_id,worker_id,stop))
    try:
        async with SessionFactory() as db:
            job=await db.get(SolutionJob,job_id)
            if job is None or job.locked_by!=worker_id:return True
            await process_generation_job(db,job)
    finally:stop.set();await heartbeat
    return True

async def run_forever()->None:
    if not worker_enabled():
        while True:await asyncio.sleep(3600)
    await init_db();worker_id=worker_identity()
    while True:
        did_work=await work_once(worker_id)
        if not did_work:await asyncio.sleep(_poll_seconds())

if __name__=="__main__":asyncio.run(run_forever())