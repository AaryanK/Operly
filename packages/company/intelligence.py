import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.company.events import append_event
from packages.database.product_models import CompanyEvidence, CompanyProfile, CompanyQuestion
from packages.database.scope_models import ProfileSubject, ScopedCompanyEvidence, ScopedCompanyProfile

PROFILE_FIELDS = ("legal_name","display_name","business_name","description","business_type","category","products_services","target_customers","locations","service_areas","markets","operating_geography","contact","brand","website","public_surfaces","acquisition_channels","primary_goals","operating_preferences","commerce_service_model")
SOURCE_WEIGHT={"owner":1.0,"business_record":.9,"connector":.85,"website":.8,"public_web":.65,"research":.55,"inference":.4}
IDENTITY_FIELDS={"legal_name","display_name","business_name","description","business_type","category","brand","website","target_customers","markets","locations"}


def _loads(value, fallback):
 try:return json.loads(value or "")
 except (TypeError,json.JSONDecodeError):return fallback


def profile_payload(row)->dict[str,Any]:
 if not row:return {"profile":{},"fields":{},"conflicts":[],"completed_at":None}
 profile=_loads(row.profile_json,{})
 if not profile and hasattr(row,"answers_json"):profile=_loads(row.answers_json,{})
 conflicts=_loads(getattr(row,"unresolved_conflicts_json","[]"),[])
 return {"profile":profile,"fields":_loads(row.field_meta_json,{}),"conflicts":conflicts,"completed_at":getattr(row,"completed_at",None).isoformat() if getattr(row,"completed_at",None) else None,"updated_at":row.updated_at.isoformat() if getattr(row,"updated_at",None) else None}


async def profile_subject(db:AsyncSession,tenant_id:str,*,kind:str="workspace",reference_id:str|None=None,display_name:str|None=None,create:bool=True)->ProfileSubject|None:
 query=select(ProfileSubject).where(ProfileSubject.tenant_id==tenant_id,ProfileSubject.kind==kind)
 query=query.where(ProfileSubject.reference_id.is_(None)) if reference_id is None else query.where(ProfileSubject.reference_id==reference_id)
 row=await db.scalar(query)
 if row or not create:return row
 row=ProfileSubject(tenant_id=tenant_id,kind=kind,reference_id=reference_id,display_name=(display_name or ("Workspace company" if kind=="workspace" else kind.title()))[:200],inherits_workspace=kind!="workspace")
 db.add(row);await db.flush()
 return row


async def scoped_profile(db:AsyncSession,tenant_id:str,*,kind:str="workspace",reference_id:str|None=None,display_name:str|None=None)->dict[str,Any]:
 subject=await profile_subject(db,tenant_id,kind=kind,reference_id=reference_id,display_name=display_name,create=False)
 if not subject:
  return {"subject":None,"profile":{},"fields":{},"conflicts":[]}
 row=await db.scalar(select(ScopedCompanyProfile).where(ScopedCompanyProfile.subject_id==subject.id,ScopedCompanyProfile.tenant_id==tenant_id))
 payload=profile_payload(row)
 payload["subject"]={"id":subject.id,"kind":subject.kind,"reference_id":subject.reference_id,"display_name":subject.display_name,"inherits_workspace":subject.inherits_workspace}
 return payload


async def observe_evidence(db:AsyncSession,tenant_id:str,field_key:str,value:Any,source_type:str,*,source_url=None,source_reference=None,confidence=.5,owner_confirmed=False,owner_initiated=False,subject_kind="workspace",subject_reference=None,subject_name=None,actor_user_id=None,conversation_id=None,action_id=None,research_run_id=None):
 if source_type not in SOURCE_WEIGHT:raise ValueError("Unsupported evidence source type")
 if field_key not in PROFILE_FIELDS:raise ValueError("Unsupported company profile field")
 serialized=json.dumps(value,sort_keys=True,ensure_ascii=False);digest=hashlib.sha256(serialized.encode()).hexdigest()
 subject=await profile_subject(db,tenant_id,kind=subject_kind,reference_id=subject_reference,display_name=subject_name,create=True)
 row=await db.scalar(select(ScopedCompanyEvidence).where(ScopedCompanyEvidence.subject_id==subject.id,ScopedCompanyEvidence.field_key==field_key,ScopedCompanyEvidence.content_hash==digest,ScopedCompanyEvidence.source_type==source_type))
 if row:
  row.confidence=max(row.confidence,float(confidence));row.owner_confirmed=row.owner_confirmed or bool(owner_confirmed);row.owner_initiated=row.owner_initiated or bool(owner_initiated);row.observed_at=datetime.utcnow()
  row.source_url=source_url or row.source_url;row.source_reference=source_reference or row.source_reference;row.actor_user_id=actor_user_id or row.actor_user_id;row.conversation_id=conversation_id or row.conversation_id;row.action_id=action_id or row.action_id;row.research_run_id=research_run_id or row.research_run_id
 else:
  row=ScopedCompanyEvidence(tenant_id=tenant_id,subject_id=subject.id,field_key=field_key,value_json=serialized,source_type=source_type,source_url=source_url,source_reference=source_reference,confidence=max(0,min(float(confidence),1)),owner_initiated=bool(owner_initiated),owner_confirmed=bool(owner_confirmed),content_hash=digest,actor_user_id=actor_user_id,conversation_id=conversation_id,action_id=action_id,research_run_id=research_run_id);db.add(row);await db.flush()
 # Mirror only explicitly workspace-scoped evidence into the legacy table while old
 # callers are being retired. Solution/research subjects can never poison it.
 if subject_kind=="workspace":
  legacy=await db.scalar(select(CompanyEvidence).where(CompanyEvidence.tenant_id==tenant_id,CompanyEvidence.field_key==field_key,CompanyEvidence.content_hash==digest,CompanyEvidence.source_type==source_type))
  if not legacy:
   legacy=CompanyEvidence(tenant_id=tenant_id,field_key=field_key,value_json=serialized,source_type=source_type if source_type!="research" else "public_web",source_url=source_url,source_reference=source_reference,confidence=max(0,min(float(confidence),1)),owner_confirmed=bool(owner_confirmed),content_hash=digest);db.add(legacy)
 await append_event(db,tenant_id=tenant_id,event_type="company.evidence.observed",payload={"evidence_id":row.id,"field":field_key,"source_type":source_type,"confidence":row.confidence,"subject_id":subject.id,"subject_kind":subject.kind,"subject_reference":subject.reference_id,"owner_initiated":row.owner_initiated,"owner_confirmed":row.owner_confirmed},source="company_intelligence")
 return row


def _rank(row):
 return (bool(row.owner_confirmed),SOURCE_WEIGHT.get(row.source_type,0),round(float(row.confidence or 0),4))


async def synthesize_profile(db:AsyncSession,tenant_id:str,*,subject_kind="workspace",subject_reference=None,subject_name=None):
 subject=await profile_subject(db,tenant_id,kind=subject_kind,reference_id=subject_reference,display_name=subject_name,create=True)
 rows=(await db.scalars(select(ScopedCompanyEvidence).where(ScopedCompanyEvidence.tenant_id==tenant_id,ScopedCompanyEvidence.subject_id==subject.id,ScopedCompanyEvidence.superseded.is_(False),ScopedCompanyEvidence.stale.is_(False)).order_by(ScopedCompanyEvidence.observed_at.desc()))).all()
 grouped={}
 for row in rows:grouped.setdefault(row.field_key,[]).append(row)
 profile={};meta={};unresolved=[]
 for key,items in grouped.items():
  ranked=sorted(items,key=lambda x:(_rank(x),x.observed_at),reverse=True)
  chosen=ranked[0];values={x.value_json for x in items};conflict=len(values)>1
  same_authority_conflict=any(x.value_json!=chosen.value_json and _rank(x)==_rank(chosen) for x in ranked[1:])
  unresolved_conflict=bool(conflict and same_authority_conflict)
  if not unresolved_conflict:profile[key]=_loads(chosen.value_json,None)
  else:unresolved.append(key)
  meta[key]={"confidence":chosen.confidence,"source_type":chosen.source_type,"evidence_id":chosen.id,"owner_initiated":chosen.owner_initiated,"owner_confirmed":chosen.owner_confirmed,"subject_id":subject.id,"subject_kind":subject.kind,"subject_reference":subject.reference_id,"conflict":conflict,"unresolved":unresolved_conflict,"alternatives":[{"evidence_id":x.id,"value":_loads(x.value_json,None),"source_type":x.source_type,"confidence":x.confidence,"owner_initiated":x.owner_initiated,"owner_confirmed":x.owner_confirmed,"source_reference":x.source_reference} for x in ranked[1:5]]}
 row=await db.scalar(select(ScopedCompanyProfile).where(ScopedCompanyProfile.tenant_id==tenant_id,ScopedCompanyProfile.subject_id==subject.id))
 if not row:row=ScopedCompanyProfile(tenant_id=tenant_id,subject_id=subject.id);db.add(row)
 row.profile_json=json.dumps(profile,sort_keys=True);row.field_meta_json=json.dumps(meta,sort_keys=True);row.unresolved_conflicts_json=json.dumps(sorted(unresolved));await db.flush()
 # Legacy CompanyProfile remains a workspace compatibility projection only. It is
 # never written from Solution/external research subjects.
 if subject_kind=="workspace":
  legacy=await db.get(CompanyProfile,tenant_id)
  if not legacy:legacy=CompanyProfile(tenant_id=tenant_id);db.add(legacy)
  legacy.profile_json=row.profile_json;legacy.field_meta_json=row.field_meta_json;legacy.answers_json=json.dumps({k:v for k,v in profile.items() if meta[k]["owner_confirmed"]},sort_keys=True);await db.flush()
 await append_event(db,tenant_id=tenant_id,event_type="company.profile.updated",payload={"fields":sorted(profile),"unresolved_conflicts":sorted(unresolved),"subject_id":subject.id,"subject_kind":subject.kind,"subject_reference":subject.reference_id},source="company_intelligence")
 for key in unresolved:await append_event(db,tenant_id=tenant_id,event_type="company.profile.conflict_detected",payload={"field":key,"subject_id":subject.id},source="company_intelligence")
 payload=profile_payload(row);payload["subject"]={"id":subject.id,"kind":subject.kind,"reference_id":subject.reference_id,"display_name":subject.display_name};return payload


async def context_for_subject(db:AsyncSession,tenant_id:str,*,subject_kind:str,subject_reference:str|None,subject_name:str|None=None)->dict[str,Any]:
 """Return subject facts plus explicitly inherited non-identity workspace facts."""
 subject=await scoped_profile(db,tenant_id,kind=subject_kind,reference_id=subject_reference,display_name=subject_name)
 workspace=await scoped_profile(db,tenant_id,kind="workspace",reference_id=None)
 inherited={}
 if subject_kind!="workspace":
  for key,value in (workspace.get("profile") or {}).items():
   field=(workspace.get("fields") or {}).get(key) or {}
   if key not in IDENTITY_FIELDS and not field.get("unresolved"):
    inherited[key]=value
 return {"subject":subject,"workspace_inherited":{"profile":inherited,"scope":"workspace","subject":workspace.get("subject")},"precedence":["owner_instruction","solution_context","solution_profile","workspace_inherited"]}


QUESTION_RULES={
 "restaurant":[("commerce_service_model","Do you want customers to order directly, reserve a table, or contact you first?","This shapes the customer journey."),("service_areas","How far do you offer delivery or catering?","This prevents inquiries you cannot serve."),("primary_goals","What matters most right now: dine-in traffic, direct orders, or catering?","This keeps recommendations focused.")],
 "dropshipper":[("markets","Which countries do you want to sell to?","Markets affect payments, shipping, and compliance."),("operating_preferences","Where are your suppliers and how ready is fulfillment?","This sets realistic launch steps."),("commerce_service_model","Which payment and storefront setup do you prefer?","This determines the selling workflow.")],
 "contractor":[("service_areas","What service radius can your team reliably cover?","This improves lead quality."),("products_services","Which high-value services do you most want to sell?","This focuses acquisition on profitable work."),("target_customers","What kinds of leads are the best fit?","This helps filter poor-fit inquiries.")],
 "default":[("primary_goals","What is the most important result you want in the next 90 days?","This helps OPERLY prioritize useful work."),("target_customers","Who is the customer you most want to reach?","This makes recommendations and messaging specific."),("commerce_service_model","How do customers buy from or work with you today?","This clarifies the right operating workflow.")],
}
async def generate_questions(db:AsyncSession,tenant_id:str):
 payload=await scoped_profile(db,tenant_id,kind="workspace");profile=payload["profile"]
 if not profile:profile=profile_payload(await db.get(CompanyProfile,tenant_id))["profile"]
 kind=str(profile.get("business_type") or profile.get("category") or "").lower()
 rules=QUESTION_RULES["dropshipper"] if "dropship" in kind else QUESTION_RULES["restaurant"] if "restaurant" in kind else QUESTION_RULES["contractor"] if any(x in kind for x in ("contractor","plumb","service")) else QUESTION_RULES["default"]
 existing=(await db.scalars(select(CompanyQuestion).where(CompanyQuestion.tenant_id==tenant_id))).all();targets={tuple(_loads(x.target_fields_json,[])) for x in existing}
 for field,question,why in rules:
  if profile.get(field) not in (None,"",[],{}) or (field,) in targets:continue
  db.add(CompanyQuestion(tenant_id=tenant_id,question=question,why_it_matters=why,target_fields_json=json.dumps([field]),business_type=kind or None))
 await db.flush();return await list_questions(db,tenant_id)

async def list_questions(db,tenant_id):
 rows=(await db.scalars(select(CompanyQuestion).where(CompanyQuestion.tenant_id==tenant_id).order_by(CompanyQuestion.answered,CompanyQuestion.created_at))).all()
 return [{"id":x.id,"question":x.question,"why_it_matters":x.why_it_matters,"target_fields":_loads(x.target_fields_json,[]),"answered":x.answered,"answer":_loads(x.answer_json,None),"owner_confirmed":x.owner_confirmed} for x in rows]

async def answer_question(db,tenant_id,question_id,answer):
 row=await db.scalar(select(CompanyQuestion).where(CompanyQuestion.id==question_id,CompanyQuestion.tenant_id==tenant_id))
 if not row:raise LookupError("Question not found")
 row.answered=True;row.owner_confirmed=True;row.answer_json=json.dumps(answer);row.answered_at=datetime.utcnow()
 for field in _loads(row.target_fields_json,[]):await observe_evidence(db,tenant_id,field,answer,"owner",confidence=1,owner_confirmed=True,owner_initiated=True,source_reference=f"question:{row.id}")
 await append_event(db,tenant_id=tenant_id,event_type="company.question.answered",payload={"question_id":row.id,"fields":_loads(row.target_fields_json,[])},actor_type="owner",source="company_intelligence")
 return await synthesize_profile(db,tenant_id)
