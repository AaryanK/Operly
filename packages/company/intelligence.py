import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.company.events import append_event
from packages.database.product_models import CompanyEvidence, CompanyProfile, CompanyQuestion

PROFILE_FIELDS = ("legal_name","display_name","business_name","description","business_type","category","products_services","target_customers","locations","service_areas","markets","operating_geography","contact","brand","website","public_surfaces","acquisition_channels","primary_goals","operating_preferences","commerce_service_model")
SOURCE_WEIGHT={"owner":1.0,"business_record":.9,"connector":.85,"website":.8,"public_web":.65,"inference":.4}

def _loads(value, fallback):
 try:return json.loads(value or "")
 except (TypeError,json.JSONDecodeError):return fallback

def profile_payload(row: CompanyProfile|None)->dict[str,Any]:
 if not row:return {"profile":{},"fields":{},"completed_at":None}
 profile=_loads(row.profile_json,{})
 if not profile:profile=_loads(row.answers_json,{}) # legacy compatibility
 return {"profile":profile,"fields":_loads(row.field_meta_json,{}),"completed_at":row.completed_at.isoformat() if row.completed_at else None,"updated_at":row.updated_at.isoformat() if row.updated_at else None}

async def observe_evidence(db:AsyncSession,tenant_id:str,field_key:str,value:Any,source_type:str,*,source_url=None,source_reference=None,confidence=.5,owner_confirmed=False):
 if source_type not in SOURCE_WEIGHT:raise ValueError("Unsupported evidence source type")
 if field_key not in PROFILE_FIELDS:raise ValueError("Unsupported company profile field")
 serialized=json.dumps(value,sort_keys=True,ensure_ascii=False);digest=hashlib.sha256(serialized.encode()).hexdigest()
 row=await db.scalar(select(CompanyEvidence).where(CompanyEvidence.tenant_id==tenant_id,CompanyEvidence.field_key==field_key,CompanyEvidence.content_hash==digest,CompanyEvidence.source_type==source_type))
 if row:
  row.confidence=max(row.confidence,float(confidence));row.owner_confirmed=row.owner_confirmed or owner_confirmed;row.observed_at=datetime.utcnow()
 else:
  row=CompanyEvidence(tenant_id=tenant_id,field_key=field_key,value_json=serialized,source_type=source_type,source_url=source_url,source_reference=source_reference,confidence=max(0,min(float(confidence),1)),owner_confirmed=owner_confirmed,content_hash=digest);db.add(row);await db.flush()
 await append_event(db,tenant_id=tenant_id,event_type="company.evidence.observed",payload={"evidence_id":row.id,"field":field_key,"source_type":source_type,"confidence":row.confidence},source="company_intelligence")
 return row

async def synthesize_profile(db:AsyncSession,tenant_id:str):
 rows=(await db.scalars(select(CompanyEvidence).where(CompanyEvidence.tenant_id==tenant_id,CompanyEvidence.superseded.is_(False),CompanyEvidence.stale.is_(False)).order_by(CompanyEvidence.observed_at.desc()))).all()
 grouped={}
 for row in rows:grouped.setdefault(row.field_key,[]).append(row)
 profile={};meta={};conflicts=[]
 for key,items in grouped.items():
  ranked=sorted(items,key=lambda x:(bool(x.owner_confirmed),SOURCE_WEIGHT.get(x.source_type,0),x.confidence,x.observed_at),reverse=True)
  chosen=ranked[0];profile[key]=_loads(chosen.value_json,None)
  values={x.value_json for x in items}
  conflict=len(values)>1
  meta[key]={"confidence":chosen.confidence,"source_type":chosen.source_type,"evidence_id":chosen.id,"owner_confirmed":chosen.owner_confirmed,"conflict":conflict,"alternatives":[{"evidence_id":x.id,"value":_loads(x.value_json,None),"source_type":x.source_type,"confidence":x.confidence,"owner_confirmed":x.owner_confirmed} for x in ranked[1:5]]}
  if conflict:conflicts.append(key)
 row=await db.get(CompanyProfile,tenant_id)
 if not row:row=CompanyProfile(tenant_id=tenant_id);db.add(row)
 row.profile_json=json.dumps(profile,sort_keys=True);row.field_meta_json=json.dumps(meta,sort_keys=True);row.answers_json=json.dumps({k:v for k,v in profile.items() if meta[k]["owner_confirmed"]},sort_keys=True);await db.flush()
 await append_event(db,tenant_id=tenant_id,event_type="company.profile.updated",payload={"fields":sorted(profile),"conflicts":conflicts},source="company_intelligence")
 for key in conflicts:await append_event(db,tenant_id=tenant_id,event_type="company.profile.conflict_detected",payload={"field":key},source="company_intelligence")
 return profile_payload(row)

QUESTION_RULES={
 "restaurant":[("commerce_service_model","Do you want customers to order directly, reserve a table, or contact you first?","This shapes the customer journey."),("service_areas","How far do you offer delivery or catering?","This prevents inquiries you cannot serve."),("primary_goals","What matters most right now: dine-in traffic, direct orders, or catering?","This keeps recommendations focused.")],
 "dropshipper":[("markets","Which countries do you want to sell to?","Markets affect payments, shipping, and compliance."),("operating_preferences","Where are your suppliers and how ready is fulfillment?","This sets realistic launch steps."),("commerce_service_model","Which payment and storefront setup do you prefer?","This determines the selling workflow.")],
 "contractor":[("service_areas","What service radius can your team reliably cover?","This improves lead quality."),("products_services","Which high-value services do you most want to sell?","This focuses acquisition on profitable work."),("target_customers","What kinds of leads are the best fit?","This helps filter poor-fit inquiries.")],
 "default":[("primary_goals","What is the most important result you want in the next 90 days?","This helps OPERLY prioritize useful work."),("target_customers","Who is the customer you most want to reach?","This makes recommendations and messaging specific."),("commerce_service_model","How do customers buy from or work with you today?","This clarifies the right operating workflow.")],
}
async def generate_questions(db:AsyncSession,tenant_id:str):
 payload=profile_payload(await db.get(CompanyProfile,tenant_id));profile=payload["profile"];kind=str(profile.get("business_type") or profile.get("category") or "").lower()
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
 for field in _loads(row.target_fields_json,[]):await observe_evidence(db,tenant_id,field,answer,"owner",confidence=1,owner_confirmed=True,source_reference=f"question:{row.id}")
 await append_event(db,tenant_id=tenant_id,event_type="company.question.answered",payload={"question_id":row.id,"fields":_loads(row.target_fields_json,[])},actor_type="owner",source="company_intelligence")
 return await synthesize_profile(db,tenant_id)
