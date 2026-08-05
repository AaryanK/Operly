"""Durable control-plane orchestration for external isolated runners."""
from __future__ import annotations
import hashlib,json,os,re
from datetime import datetime,timedelta
from sqlalchemy import func,select
from packages.custom_software.generated_sources import generated_files,prompt_digest
from packages.custom_software.runner_adapters import ExternalRunnerAdapter,RunnerAdapter
from packages.custom_software.runner_contracts import BuildSubmission,HealthCheck,NetworkPolicy,ResourcePolicy
from packages.custom_software.runtime_profiles import runtime_profile
from packages.custom_software.source_bundles import SourceFile,build_bundle
from packages.database.custom_software_models import GeneratedSourceBundle,RunnerArtifactRecord,RunnerBuildEvent,RunnerBuildRecord,RunnerPreviewRecord,RunnerRepairRecord

STATES={"created","queued","provisioning","provision_failed","source_staging","dependency_resolution","dependency_failed","static_analysis","static_analysis_failed","building","build_failed","testing","tests_failed","starting","start_failed","health_checking","health_check_failed","acceptance_testing","acceptance_failed","running","preview_ready","repair_requested","repairing","repair_failed","cancel_requested","cancelled","timed_out","security_blocked","resource_exceeded","cleaning","cleaned","completed","failed"}
TERMINAL={"cleaned","completed","failed","cancelled","timed_out","security_blocked","resource_exceeded"}
TRANSITIONS={
 "created":{"queued","cancel_requested"},"queued":{"provisioning","failed","cancel_requested"},"provisioning":{"source_staging","provision_failed","cancel_requested"},"source_staging":{"dependency_resolution","static_analysis","security_blocked","failed"},"dependency_resolution":{"static_analysis","dependency_failed","security_blocked"},"static_analysis":{"building","static_analysis_failed","security_blocked"},"building":{"testing","build_failed","resource_exceeded","timed_out"},"testing":{"starting","tests_failed","resource_exceeded","timed_out"},"starting":{"health_checking","start_failed","resource_exceeded"},"health_checking":{"acceptance_testing","health_check_failed","timed_out"},"acceptance_testing":{"running","acceptance_failed","timed_out"},"running":{"preview_ready","cancel_requested","cleaning"},"preview_ready":{"repair_requested","cancel_requested","cleaning","completed"},"repair_requested":{"repairing","cancel_requested"},"repairing":{"queued","repair_failed","security_blocked"},"cancel_requested":{"cancelled"},"cancelled":{"cleaning"},"completed":{"cleaning"},"failed":{"repair_requested","cleaning"},"build_failed":{"repair_requested","cleaning","failed"},"tests_failed":{"repair_requested","cleaning","failed"},"start_failed":{"repair_requested","cleaning","failed"},"health_check_failed":{"repair_requested","cleaning","failed"},"acceptance_failed":{"repair_requested","cleaning","failed"},"cleaning":{"cleaned"},"provision_failed":{"failed","cleaning"},"dependency_failed":{"failed","repair_requested","cleaning"},"static_analysis_failed":{"failed","repair_requested","cleaning"},"repair_failed":{"failed","cleaning"}}
FAILURES={"provision_failed":"provision_failure","dependency_failed":"dependency_failure","static_analysis_failed":"security_policy_violation","build_failed":"build_failure","tests_failed":"test_failure","start_failed":"runtime_crash","health_check_failed":"health_check_failure","acceptance_failed":"acceptance_test_failure","security_blocked":"security_policy_violation","resource_exceeded":"resource_violation","timed_out":"resource_violation","failed":"unknown_failure"}
class RunnerStateError(ValueError):pass

def _redact(value):return re.sub(r"(?i)(bearer\s+|token[=:]\s*|secret[=:]\s*)[^\s,]+",r"\1[REDACTED]",str(value))[:4000]
async def _event(db,row,state,event_type="lifecycle",message="",details=None):
 if state not in STATES:raise RunnerStateError("Unknown runner state")
 if state!=row.state and state not in TRANSITIONS.get(row.state,set()):raise RunnerStateError(f"Invalid runner transition {row.state} -> {state}")
 sequence=(await db.scalar(select(func.max(RunnerBuildEvent.sequence)).where(RunnerBuildEvent.build_id==row.id)) or 0)+1
 row.state=state
 if state in FAILURES:row.failure_classification=FAILURES[state]
 safe={k:_redact(v) for k,v in (details or {}).items()}
 db.add(RunnerBuildEvent(tenant_id=row.tenant_id,build_id=row.id,sequence=sequence,state=state,event_type=event_type,message=_redact(message or state),details_json=json.dumps(safe)))

async def create_source(db,tenant_id,user_id,plan_row,plan,defect=False,parent=None):
 application_id=f"plan-{plan_row.id}";version=(await db.scalar(select(func.max(GeneratedSourceBundle.source_version)).where(GeneratedSourceBundle.tenant_id==tenant_id,GeneratedSourceBundle.application_id==application_id)) or 0)+1
 bundle=build_bundle(generated_files(plan,defect),tenant_id,application_id,plan_row.id,plan_row.approved_version,version,prompt_digest(plan))
 provenance={"originalPrompt":plan.provenance.get("originalPrompt",plan.summary),"revisions":plan.provenance.get("revisions",[]),"planId":plan_row.id,"planVersion":plan_row.approved_version,"parentSourceBundleId":parent.id if parent else None,"secretValuesStored":False}
 row=GeneratedSourceBundle(tenant_id=tenant_id,plan_id=plan_row.id,plan_version=plan_row.approved_version,source_version=version,application_id=application_id,bundle_digest=bundle.digest,manifest_json=json.dumps(bundle.manifest),files_json=json.dumps([{"path":x.path,"content":x.content.decode(),"generatedBy":x.generated_by} for x in bundle.files]),provenance_json=json.dumps(provenance),created_by=user_id);db.add(row);await db.flush();return row,bundle

def _submission(source,plan,idempotency):
 profile_id=plan.stack.runtime if plan.stack else ""
 profile=runtime_profile(profile_id);resources=ResourcePolicy.model_validate(profile["resources"])
 return BuildSubmission(workspaceId=source.tenant_id,applicationId=source.application_id,planVersion=source.plan_version,sourceVersion=source.source_version,stackId=profile_id,sourceBundleDigest=source.bundle_digest,operations=profile["operations"],healthCheck=HealthCheck.model_validate(profile["health"]),resources=resources,network=NetworkPolicy(mode="none"),requiredPorts=profile["ports"],artifactPaths=profile["artifactPaths"],maxDurationSeconds=resources.durationSeconds,idempotencyKey=idempotency)

async def apply_runner_response(db,row,response,submission):
 row.runner_job_id=response.get("jobId",row.runner_job_id);result=response.get("result",{});state=response.get("state","failed")
 if state in {"created","queued","provisioning","source_staging","dependency_resolution","static_analysis","building","testing","starting","health_checking","acceptance_testing","running"} and not result:
  if state!=row.state:
   # Signed runner polling can advance only through declared transitions.
   await _event(db,row,state,event_type="runner_poll",message=f"Runner state: {state}")
  row.result_json=json.dumps({"remoteState":state});await db.commit();await db.refresh(row);return row
 classification=result.get("failureEvidence",{}).get("classification")
 phase_paths={"build_failure":["provisioning","source_staging","static_analysis","building"],"test_failure":["provisioning","source_staging","static_analysis","building","testing"],"runtime_crash":["provisioning","source_staging","static_analysis","building","testing","starting"],"health_check_failure":["provisioning","source_staging","static_analysis","building","testing","starting","health_checking"],"acceptance_test_failure":["provisioning","source_staging","static_analysis","building","testing","starting","health_checking","acceptance_testing"],"security_policy_violation":["provisioning","source_staging"],"resource_violation":["provisioning","source_staging","static_analysis","building"]}
 path=phase_paths.get(classification,["provisioning","source_staging","static_analysis","building","testing","starting","health_checking","acceptance_testing"])
 for phase in path:
  if phase!=row.state:await _event(db,row,phase,message=f"Runner phase: {phase}")
 if state=="preview_ready" and all(result.get(k) for k in ("buildSuccess","testSuccess","processStartSuccess","healthCheckSuccess","acceptanceCheckSuccess","previewAvailable")):
  await _event(db,row,"running",message="Generated application process is running");await _event(db,row,"preview_ready",message="Health and acceptance checks passed")
  preview=response["preview"];existing=await db.scalar(select(RunnerPreviewRecord).where(RunnerPreviewRecord.build_id==row.id,RunnerPreviewRecord.runner_preview_id==preview["id"]))
  if not existing:db.add(RunnerPreviewRecord(tenant_id=row.tenant_id,build_id=row.id,runner_preview_id=preview["id"],target_url=preview["targetUrl"],expires_at=datetime.utcnow()+timedelta(seconds=submission.resources.previewSeconds),created_by=row.created_by))
 else:
  classification=classification or "unknown_failure";failure_state={"test_failure":"tests_failed","build_failure":"build_failed","runtime_crash":"start_failed","health_check_failure":"health_check_failed","acceptance_test_failure":"acceptance_failed","security_policy_violation":"security_blocked","resource_violation":"resource_exceeded"}.get(classification,"failed");await _event(db,row,failure_state,event_type="failure",message="Runner quality gate failed",details=result.get("failureEvidence",{}))
 row.result_json=json.dumps(result);row.started_at=row.started_at or row.created_at;row.completed_at=None if row.state=="preview_ready" else datetime.utcnow()
 existing_artifacts=await db.scalar(select(func.count(RunnerArtifactRecord.id)).where(RunnerArtifactRecord.build_id==row.id))
 if not existing_artifacts:
  for artifact in result.get("artifacts",[]):db.add(RunnerArtifactRecord(tenant_id=row.tenant_id,build_id=row.id,kind=artifact.get("kind","output"),name=artifact.get("name","artifact"),digest=artifact.get("digest","sha256:"+"0"*64),size_bytes=artifact.get("sizeBytes",0),reference=artifact.get("reference","runner"),metadata_json=json.dumps(artifact)))
 await db.commit();await db.refresh(row);return row

async def submit_build(db,tenant_id,user_id,plan_row,plan,idempotency,adapter:RunnerAdapter|None=None,defect=False,parent=None):
 existing=await db.scalar(select(RunnerBuildRecord).where(RunnerBuildRecord.tenant_id==tenant_id,RunnerBuildRecord.idempotency_key==idempotency))
 if existing:return existing
 parent_source=await db.get(GeneratedSourceBundle,parent.source_bundle_id) if parent else None
 source,bundle=await create_source(db,tenant_id,user_id,plan_row,plan,defect,parent_source)
 adapter=adapter or ExternalRunnerAdapter();submission=_submission(source,plan,idempotency)
 row=RunnerBuildRecord(tenant_id=tenant_id,plan_id=plan_row.id,source_bundle_id=source.id,idempotency_key=idempotency,state="created",runner_implementation=adapter.implementation,isolation_profile=adapter.isolation_profile,submission_json=submission.model_dump_json(),attempt=1 if not parent else parent.attempt+1,parent_build_id=parent.id if parent else None,created_by=user_id);db.add(row);await db.flush();await _event(db,row,"created",message="Build record created");await _event(db,row,"queued",message="Submitted to isolated runner");await db.commit()
 try:response=await adapter.submit(submission,bundle)
 except Exception as error:
  await _event(db,row,"failed",event_type="runner_unavailable",message="runner_unavailable",details={"message":str(error)});row.result_json=json.dumps({"code":"runner_unavailable"});row.completed_at=datetime.utcnow();await db.commit();return row
 return await apply_runner_response(db,row,response,submission)

async def owned_build(db,tenant_id,build_id):
 row=await db.get(RunnerBuildRecord,build_id)
 if not row or row.tenant_id!=tenant_id:raise LookupError("Runner build not found")
 return row
async def refresh_build(db,row,adapter=None):
 if not row.runner_job_id or row.state in TERMINAL|{"preview_ready"}:return row
 adapter=adapter or ExternalRunnerAdapter();response=await adapter.status(row.runner_job_id);submission=BuildSubmission.model_validate_json(row.submission_json);return await apply_runner_response(db,row,response,submission)
async def build_events(db,row):return list((await db.scalars(select(RunnerBuildEvent).where(RunnerBuildEvent.build_id==row.id,RunnerBuildEvent.tenant_id==row.tenant_id).order_by(RunnerBuildEvent.sequence))).all())
async def active_preview(db,tenant_id,preview_id):
 row=await db.get(RunnerPreviewRecord,preview_id)
 if not row or row.tenant_id!=tenant_id or row.state!="active" or row.expires_at<=datetime.utcnow():raise LookupError("Active preview not found")
 build=await owned_build(db,tenant_id,row.build_id)
 if build.state!="preview_ready":raise LookupError("Preview build is not running")
 return row,build
async def stop_preview(db,row,build,adapter):
 await adapter.stop_preview(row.runner_preview_id);row.state="stopped";row.stopped_at=datetime.utcnow();await _event(db,build,"cleaning",message="Preview termination requested");await _event(db,build,"cleaned",message="Runner resources cleaned");build.completed_at=datetime.utcnow();await db.commit()

def build_json(row):
 submission=json.loads(row.submission_json)
 return {"id":row.id,"planId":row.plan_id,"sourceBundleId":row.source_bundle_id,"sourceVersion":submission.get("sourceVersion"),"runnerJobId":row.runner_job_id,"state":row.state,"runnerImplementation":row.runner_implementation,"isolationProfile":row.isolation_profile,"attempt":row.attempt,"failureClassification":row.failure_classification,"resourcePolicy":submission.get("resources"),"networkPolicy":submission.get("network"),"operations":submission.get("operations",[]),"result":json.loads(row.result_json)}

async def request_repair(db,tenant_id,user_id,build,plan_row,plan,idempotency,adapter=None):
 if build.state not in {"build_failed","tests_failed","start_failed","health_check_failed","acceptance_failed","failed"}:raise RunnerStateError("Only failed builds can be repaired")
 if build.failure_classification=="security_policy_violation":raise RunnerStateError("Security policy violations cannot be auto-repaired by weakening policy")
 if build.attempt>=3:raise RunnerStateError("Automated repair attempt limit reached")
 source=await db.get(GeneratedSourceBundle,build.source_bundle_id);failure=json.loads(build.result_json).get("failureEvidence",{})
 prompt=f"Repair generated source for approved plan {plan_row.id}. Classification: {build.failure_classification}. Evidence: {_redact(failure)}. Preserve security and make the smallest source-only patch."
 patch=[{"op":"replace","path":"app.py","reason":"restore standings win points invariant","before":"points += 2","after":"points += 3"}]
 repair=RunnerRepairRecord(tenant_id=tenant_id,build_id=build.id,source_bundle_id=source.id,attempt=build.attempt+1,classification=build.failure_classification or "unknown_failure",repair_prompt=prompt,patch_json=json.dumps(patch),created_by=user_id);db.add(repair);await _event(db,build,"repair_requested",message="Repair requested");await _event(db,build,"repairing",message="Minimal source patch generated");await db.commit()
 child=await submit_build(db,tenant_id,user_id,plan_row,plan,idempotency,adapter=adapter,defect=False,parent=build);repair.status="applied" if child.state=="preview_ready" else "failed";await db.commit();return child,repair
