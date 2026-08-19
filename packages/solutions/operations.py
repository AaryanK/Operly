import hashlib,json,re
from datetime import datetime,timedelta
from pathlib import Path
from sqlalchemy import desc,select
from packages.company.events import append_event
from packages.company.intelligence import profile_payload
from packages.database.models import ScheduledJob
from packages.database.product_models import CompanyEvidence,CompanyProfile,PresenceObservation,SolutionDeployment,SolutionImprovementProposal
from packages.database.studio_models import StudioVersion
from packages.solutions.production import JobStatus,ProductionService
from packages.studio.schema import SiteSchema
from packages.studio.service import StudioService

IMPORTANT_FACTS=("products_services","contact","service_areas","locations","description")
def _json(value,fallback):
 try:return json.loads(value or "")
 except (TypeError,json.JSONDecodeError):return fallback
def _terms(value):
 if value is None:return []
 if isinstance(value,dict):return [term for child in value.values() for term in _terms(child)]
 values=value if isinstance(value,list) else [value]
 return [re.sub(r"\s+"," ",str(x)).strip() for x in values if x is not None and str(x).strip()]
def proposal_json(row):
 return {"id":row.id,"solution_id":row.solution_id,"observation_id":row.observation_id,"action_id":row.action_id,"status":row.status,"issue":row.issue,"supporting_evidence":_json(row.supporting_evidence_json,{}),"affected_profile_facts":_json(row.affected_facts_json,[]),"affected_presence_artifacts":_json(row.affected_artifacts_json,[]),"proposed_change":_json(row.proposed_change_json,{}),"expected_outcome":row.expected_outcome,"risk":row.risk,"approval_required":row.approval_required,"before_version":row.before_version_reference,"after_version":row.after_version_reference,"deployment_id":row.deployment_id,"approved_by":row.approved_by,"verification":_json(row.verification_json,{}),"created_at":row.created_at.isoformat()}

class PresenceOperationsService:
 def __init__(self,solutions,production=None,*,stale_days=180,interval_minutes=360):
  self.solutions=solutions;self.production=production or ProductionService(solutions);self.stale_days=max(15,min(int(stale_days),10080));self.interval_minutes=max(15,min(int(interval_minutes),10080))
 async def schedule(self,db,tenant_id,solution_id,user_id=0):
  await self.solutions.get(db,tenant_id,solution_id)
  existing=await db.scalar(select(ScheduledJob).where(ScheduledJob.tenant_id==tenant_id,ScheduledJob.job_type=="presence.observe",ScheduledJob.content==solution_id,ScheduledJob.status.in_(["pending","running"])))
  if existing:return existing,False
  row=ScheduledJob(tenant_id=tenant_id,channel_id=0,user_id=int(user_id) if str(user_id).isdigit() else 0,job_type="presence.observe",content=solution_id,delivery="system",run_at=datetime.utcnow()+timedelta(minutes=self.interval_minutes),status="pending");db.add(row);await db.flush();return row,True
 async def observe(self,db,tenant_id,solution_id,*,force=False):
  solution,runtime=await self.solutions.resolve(db,tenant_id,solution_id)
  active=await db.scalar(select(SolutionDeployment).where(SolutionDeployment.tenant_id==tenant_id,SolutionDeployment.solution_id==solution_id,SolutionDeployment.status=="active").order_by(desc(SolutionDeployment.deployed_at)))
  if not active:return {"healthy":False,"observations":[],"proposals":[],"reason":"not_live"}
  artifact=Path(active.artifact_reference);available=artifact.is_file();html=artifact.read_text(encoding="utf-8") if available else "";lower=html.casefold()
  health={"available":available,"http_status":200 if available else 503,"doctype":lower.lstrip().startswith("<!doctype html>"),"application_error":bool(re.search(r"internal server error|traceback|application error",lower)),"contact_form":'data-form-key=' in lower and '<form' in lower}
  await append_event(db,tenant_id=tenant_id,event_type="presence.live" if all((health["available"],health["doctype"],not health["application_error"])) else "presence.health_failed",payload={"solution_id":solution_id,"deployment_id":active.id,"version":active.version_reference,"health":health},source="presence_observer")
  profile=profile_payload(await db.get(CompanyProfile,tenant_id))["profile"];missing=[]
  for field in IMPORTANT_FACTS:
   for term in _terms(profile.get(field)):
    if term.casefold() not in lower:missing.append({"field":field,"value":term})
  stale=[];cutoff=datetime.utcnow()-timedelta(days=self.stale_days)
  for field in IMPORTANT_FACTS:
   if profile.get(field) and not await db.scalar(select(CompanyEvidence.id).where(CompanyEvidence.tenant_id==tenant_id,CompanyEvidence.field_key==field,CompanyEvidence.stale.is_(False),CompanyEvidence.observed_at>=cutoff)):stale.append(field)
  findings=[]
  if missing:findings.append(("profile_mismatch",{"missing":missing,"profile_version":profile,"deployment_id":active.id}))
  if stale:findings.append(("stale_business_information",{"stale_fields":stale,"stale_after_days":self.stale_days}))
  if not health["contact_form"]:findings.append(("form_failed",{"reason":"published contact form marker is missing"}))
  observations=[];proposals=[]
  for kind,evidence in findings:
   fingerprint=hashlib.sha256(json.dumps({"version":active.version_reference,"kind":kind,"evidence":evidence},sort_keys=True).encode()).hexdigest()
   observation=await db.scalar(select(PresenceObservation).where(PresenceObservation.tenant_id==tenant_id,PresenceObservation.solution_id==solution_id,PresenceObservation.observation_type==kind,PresenceObservation.fingerprint==fingerprint))
   if not observation:
    observation=PresenceObservation(tenant_id=tenant_id,solution_id=solution_id,observation_type=kind,status="open",fingerprint=fingerprint,evidence_json=json.dumps(evidence,sort_keys=True),observed_version_reference=active.version_reference);db.add(observation);await db.flush()
    event_type={"profile_mismatch":"presence.profile_mismatch","form_failed":"presence.form_failed","stale_business_information":"presence.content_drift"}[kind];await append_event(db,tenant_id=tenant_id,event_type=event_type,payload={"solution_id":solution_id,"observation_id":observation.id,**evidence},source="presence_observer")
   observations.append(observation)
   if kind=="profile_mismatch":
    proposal=await db.scalar(select(SolutionImprovementProposal).where(SolutionImprovementProposal.tenant_id==tenant_id,SolutionImprovementProposal.observation_id==observation.id))
    if not proposal:
     labels=[x["value"] for x in missing];issue=f"Your business profile includes {', '.join(labels)}, but the live website does not."
     change={"operation":"sync_profile_facts","facts":missing,"summary":f"Add {', '.join(labels)} to the relevant website sections."}
     proposal=SolutionImprovementProposal(tenant_id=tenant_id,solution_id=solution_id,observation_id=observation.id,issue=issue,supporting_evidence_json=json.dumps({"owner_confirmed_profile":missing,"live_version":active.version_reference,"live_artifact_digest":active.artifact_digest},sort_keys=True),affected_facts_json=json.dumps(sorted({x["field"] for x in missing})),affected_artifacts_json=json.dumps(["Services section" if any(x["field"]=="products_services" for x in missing) else "Public website"]),proposed_change_json=json.dumps(change,sort_keys=True),expected_outcome="The live website reflects the owner-confirmed CompanyProfile while remaining healthy.",risk="medium",approval_required=True,before_version_reference=active.version_reference);db.add(proposal);await db.flush();await append_event(db,tenant_id=tenant_id,event_type="solution.change.proposed",payload={"solution_id":solution_id,"proposal_id":proposal.id,"issue":issue,"change":change,"evidence":_json(proposal.supporting_evidence_json,{})},source="presence_operations")
    proposals.append(proposal)
  await self.schedule(db,tenant_id,solution_id)
  return {"healthy":health["available"] and health["doctype"] and not health["application_error"],"health":health,"observations":[{"id":x.id,"type":x.observation_type,"status":x.status,"evidence":_json(x.evidence_json,{})} for x in observations],"proposals":[proposal_json(x) for x in proposals]}
 async def apply(self,db,tenant_id,proposal_id,actor_id):
  proposal=await db.scalar(select(SolutionImprovementProposal).where(SolutionImprovementProposal.id==proposal_id,SolutionImprovementProposal.tenant_id==tenant_id))
  if not proposal:raise LookupError("Improvement proposal not found")
  if proposal.status=="verified":return proposal
  if proposal.status not in ("proposed","approved"):raise ValueError("Proposal cannot be applied")
  solution,runtime=await self.solutions.resolve(db,tenant_id,proposal.solution_id);before=await db.get(StudioVersion,proposal.before_version_reference)
  if not before or before.project_id!=runtime.id:raise LookupError("Before version not found")
  schema=SiteSchema.model_validate_json(before.schema_json).model_dump(mode="json");facts=_json(proposal.proposed_change_json,{}).get("facts",[])
  services=[x["value"] for x in facts if x["field"]=="products_services"]
  if services:
   page=schema["pages"][0];section=next((x for x in page["sections"] if x["type"]=="service_grid"),None)
   if not section:section={"id":"services","type":"service_grid","enabled":True,"props":{"heading":"What we offer","description":"","items":[],"source_mode":"manual","catalog_item_ids":[]}};page["sections"].insert(-2 if len(page["sections"])>=2 else len(page["sections"]),section)
   titles={x["title"].casefold() for x in section["props"]["items"]}
   for value in services:
    if value.casefold() not in titles:section["props"]["items"].append({"title":value,"description":"","image_asset_id":None})
  version=await StudioService.save_schema(db,tenant_id,runtime.id,actor_id,schema,_json(proposal.proposed_change_json,{}).get("summary","Apply approved profile update"));proposal.status="approved";proposal.approved_by=actor_id;proposal.after_version_reference=version.id
  await append_event(db,tenant_id=tenant_id,event_type="solution.change.applied",payload={"solution_id":solution.id,"proposal_id":proposal.id,"before_version":proposal.before_version_reference,"after_version":version.id,"approved_by":actor_id},actor_type="owner",actor_id=actor_id,source="presence_operations")
  job,_=await self.production.publish(db,tenant_id,solution.id,actor_id,idempotency_key=f"proposal:{proposal.id}:publish",version_reference=version.id)
  deployment=await db.scalar(select(SolutionDeployment).where(SolutionDeployment.tenant_id==tenant_id,SolutionDeployment.solution_id==solution.id,SolutionDeployment.version_reference==version.id,SolutionDeployment.status=="active"))
  html=Path(deployment.artifact_reference).read_text(encoding="utf-8").casefold() if deployment and Path(deployment.artifact_reference).is_file() else "";targets=[x["value"] for x in facts];passed=job.status==JobStatus.SUCCEEDED and bool(deployment) and all(x.casefold() in html for x in targets)
  verification={"passed":passed,"target_facts":targets,"target_facts_present":all(x.casefold() in html for x in targets),"site_healthy":bool(deployment and deployment.health_state=="healthy"),"publish_job_id":job.id,"deployment_id":deployment.id if deployment else None}
  proposal.verification_json=json.dumps(verification,sort_keys=True);proposal.deployment_id=deployment.id if deployment else None;proposal.status="verified" if passed else "verification_failed"
  await append_event(db,tenant_id=tenant_id,event_type="solution.change.verified" if passed else "solution.change.verification_failed",payload={"solution_id":solution.id,"proposal_id":proposal.id,"before_version":proposal.before_version_reference,"after_version":version.id,**verification},source="presence_operations")
  return proposal

async def run_due_observations(db,solutions,*,now=None,limit=20):
 now=now or datetime.utcnow();rows=(await db.scalars(select(ScheduledJob).where(ScheduledJob.job_type=="presence.observe",ScheduledJob.status=="pending",ScheduledJob.run_at<=now).order_by(ScheduledJob.run_at).limit(max(1,min(limit,100))))).all();results=[]
 service=PresenceOperationsService(solutions)
 for job in rows:
  duplicate=await db.scalar(select(ScheduledJob.id).where(ScheduledJob.tenant_id==job.tenant_id,ScheduledJob.job_type==job.job_type,ScheduledJob.content==job.content,ScheduledJob.status=="running",ScheduledJob.id!=job.id))
  if duplicate:continue
  job.status="running";await db.flush()
  try:
   results.append(await service.observe(db,job.tenant_id,job.content));job.status="completed";await db.flush();await service.schedule(db,job.tenant_id,job.content,job.user_id)
  except Exception as error:
   job.status="failed";await append_event(db,tenant_id=job.tenant_id,event_type="presence.health_failed",payload={"solution_id":job.content,"scheduler_error":str(error)[:500]},source="presence_scheduler")
 return results
