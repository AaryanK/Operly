import json
from enum import StrEnum
from typing import Any

from sqlalchemy import desc,select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.company.events import append_event
from packages.company.intelligence import profile_payload
from packages.database.application_builder_models import ApplicationVersion,ManagedApplication
from packages.database.custom_software_models import GeneratedProject,GeneratedSourceBundle,RunnerBuildRecord,RunnerPreviewRecord
from packages.database.product_models import CompanyProfile,SolutionDeployment,SolutionRecord
from packages.database.studio_models import StudioDeployment,StudioProject,StudioVersion
from packages.studio.schema import SiteSchema,blank_site
from packages.studio.service import StudioService

class LifecycleStatus(StrEnum):
 DRAFT="draft";PLANNING="planning";BUILDING="building";PREVIEW_READY="preview_ready";APPROVED="approved";PUBLISHING="publishing";LIVE="live";DEGRADED="degraded";FAILED="failed";ARCHIVED="archived"
class RuntimeType(StrEnum):STUDIO="studio";MANAGED_APP="managed_app";GENERATED_PROJECT="generated_project"
class SolutionType(StrEnum):DIGITAL_PRESENCE="digital_presence";BUSINESS_APP="business_app";CUSTOM_SOLUTION="custom_solution"

def solution_json(row):
 preview=row.preview_url.replace("{solution_id}",row.id) if row.preview_url else None
 return {"id":row.id,"name":row.name,"description":row.description,"solution_type":row.solution_type,"status":row.lifecycle_status,"current_version":row.current_version_reference,"preview":{"state":row.preview_state,"url":preview},"production":{"state":row.production_state,"url":row.production_url},"visibility":row.visibility,"created_at":row.created_at.isoformat(),"updated_at":row.updated_at.isoformat()}

class SolutionService:
 async def _record(self,db,tenant_id,runtime_type,runtime_reference,**values):
  row=await db.scalar(select(SolutionRecord).where(SolutionRecord.tenant_id==tenant_id,SolutionRecord.runtime_type==runtime_type,SolutionRecord.runtime_reference==runtime_reference))
  if not row:row=SolutionRecord(tenant_id=tenant_id,runtime_type=runtime_type,runtime_reference=runtime_reference,**values);db.add(row);await db.flush()
  else:
   for key,value in values.items():setattr(row,key,value)
  return row
 async def sync(self,db:AsyncSession,tenant_id:str):
  studios=(await db.scalars(select(StudioProject).where(StudioProject.tenant_id==tenant_id))).all()
  for p in studios:
   deployment=await db.scalar(select(StudioDeployment).where(StudioDeployment.tenant_id==tenant_id,StudioDeployment.project_id==p.id,StudioDeployment.status=="active"))
   existing=await db.scalar(select(SolutionRecord).where(SolutionRecord.tenant_id==tenant_id,SolutionRecord.runtime_type==RuntimeType.STUDIO,SolutionRecord.runtime_reference==p.id))
   production=await db.scalar(select(SolutionDeployment).where(SolutionDeployment.tenant_id==tenant_id,SolutionDeployment.solution_id==existing.id,SolutionDeployment.status=="active").order_by(desc(SolutionDeployment.deployed_at))) if existing else None
   live=bool(production or (p.published_version_id and deployment));status=LifecycleStatus.ARCHIVED if p.status=="archived" else LifecycleStatus.LIVE if live else LifecycleStatus.PREVIEW_READY if p.active_draft_version_id else LifecycleStatus.DRAFT
   await self._record(db,tenant_id,RuntimeType.STUDIO,p.id,name=p.name,description=p.description,solution_type=existing.solution_type if existing else SolutionType.DIGITAL_PRESENCE,lifecycle_status=status,current_version_reference=production.version_reference if production else p.published_version_id or p.active_draft_version_id,preview_state="ready" if p.active_draft_version_id else "unavailable",preview_url=f"/api/solutions/{{solution_id}}/preview" if p.active_draft_version_id else None,production_state="live" if live else "offline",production_url=production.public_url if production else f"/sites/{deployment.public_slug}" if deployment else None,visibility="public" if live else "private",context_json=existing.context_json if existing else "{}")
  apps=(await db.scalars(select(ManagedApplication).where(ManagedApplication.tenant_id==tenant_id))).all()
  for app in apps:await self._record(db,tenant_id,RuntimeType.MANAGED_APP,app.id,name=app.name,description=app.description,solution_type=SolutionType.BUSINESS_APP,lifecycle_status=LifecycleStatus.PREVIEW_READY if app.active_version_id else LifecycleStatus.DRAFT,current_version_reference=app.active_version_id,preview_state="ready" if app.active_version_id else "unavailable",preview_url=f"/api/solutions/{{solution_id}}/preview" if app.active_version_id else None,production_state="offline",production_url=None,visibility="private",context_json="{}")
  projects=(await db.scalars(select(GeneratedProject).where(GeneratedProject.tenant_id==tenant_id))).all()
  for p in projects:
   preview=None
   if p.plan_id:
    build=await db.scalar(select(RunnerBuildRecord).where(RunnerBuildRecord.tenant_id==tenant_id,RunnerBuildRecord.plan_id==p.plan_id).order_by(desc(RunnerBuildRecord.created_at)))
    if build:preview=await db.scalar(select(RunnerPreviewRecord).where(RunnerPreviewRecord.tenant_id==tenant_id,RunnerPreviewRecord.build_id==build.id,RunnerPreviewRecord.state=="active"))
   await self._record(db,tenant_id,RuntimeType.GENERATED_PROJECT,p.id,name=p.name,description=p.prompt[:4000],solution_type=SolutionType.CUSTOM_SOLUTION,lifecycle_status=LifecycleStatus.PREVIEW_READY if preview else LifecycleStatus.APPROVED,current_version_reference=str(p.version),preview_state="ready" if preview else "available",preview_url=f"/api/solutions/{{solution_id}}/preview",production_state="offline",production_url=None,visibility="private",context_json="{}")
  await db.flush()
 async def list(self,db,tenant_id):await self.sync(db,tenant_id);return (await db.scalars(select(SolutionRecord).where(SolutionRecord.tenant_id==tenant_id,SolutionRecord.lifecycle_status!=LifecycleStatus.ARCHIVED).order_by(desc(SolutionRecord.updated_at)))).all()
 async def get(self,db,tenant_id,solution_id):
  await self.sync(db,tenant_id);row=await db.scalar(select(SolutionRecord).where(SolutionRecord.id==solution_id,SolutionRecord.tenant_id==tenant_id))
  if not row:raise LookupError("Solution not found")
  return row
 async def resolve(self,db,tenant_id,solution_id):
  row=await self.get(db,tenant_id,solution_id);models={RuntimeType.STUDIO:StudioProject,RuntimeType.MANAGED_APP:ManagedApplication,RuntimeType.GENERATED_PROJECT:GeneratedProject};model=models[RuntimeType(row.runtime_type)]
  runtime=await db.scalar(select(model).where(model.id==row.runtime_reference,model.tenant_id==tenant_id))
  if not runtime:raise LookupError("Solution runtime not found")
  return row,runtime
 async def versions(self,db,tenant_id,solution_id):
  row,runtime=await self.resolve(db,tenant_id,solution_id)
  if row.runtime_type==RuntimeType.STUDIO:
   items=(await db.scalars(select(StudioVersion).where(StudioVersion.tenant_id==tenant_id,StudioVersion.project_id==runtime.id).order_by(desc(StudioVersion.version_number)))).all();return [{"id":x.id,"version":x.version_number,"status":x.status,"summary":x.change_summary,"created_at":x.created_at.isoformat()} for x in items]
  if row.runtime_type==RuntimeType.MANAGED_APP:
   items=(await db.scalars(select(ApplicationVersion).where(ApplicationVersion.tenant_id==tenant_id,ApplicationVersion.application_id==runtime.id).order_by(desc(ApplicationVersion.version_number)))).all();return [{"id":x.id,"version":x.version_number,"status":"active" if x.active else "superseded","summary":x.summary,"created_at":x.created_at.isoformat()} for x in items]
  return [{"id":f"{runtime.id}:{runtime.version}","version":runtime.version,"status":"current","summary":"Current generated version","created_at":runtime.created_at.isoformat()}]
 async def create_presence(self,db,tenant_id,user_id,name=None):
  profile=profile_payload(await db.get(CompanyProfile,tenant_id))["profile"] or {}
  existing=await db.scalar(select(SolutionRecord).where(SolutionRecord.tenant_id==tenant_id,SolutionRecord.solution_type==SolutionType.DIGITAL_PRESENCE,SolutionRecord.lifecycle_status!=LifecycleStatus.ARCHIVED))
  if existing:return await self.get(db,tenant_id,existing.id)
  business=(name or profile.get("display_name") or profile.get("business_name") or profile.get("legal_name") or "Untitled Website").strip()[:200]
  description=str(profile.get("description") or "")[:500];site=blank_site(business,description).model_dump(mode="json");page=site["pages"][0];services=profile.get("products_services") or []
  if isinstance(services,str):services=[services]
  if services:page["sections"].append({"id":"services","type":"service_grid","props":{"heading":"What we offer","description":"","items":[{"title":str(x)[:180],"description":""} for x in services[:12]],"source_mode":"manual","catalog_item_ids":[]}})
  contact=profile.get("contact") or {};public_contact=", ".join((contact.get("emails",[])+contact.get("phones",[]))[:4]) if isinstance(contact,dict) else str(contact)
  page["sections"].append({"id":"contact","type":"contact_form","props":{"heading":"Contact us","description":"Tell us how we can help.","form_key":"contact","submit_button_text":"Send inquiry","success_message":"Thanks — we'll be in touch."}});page["sections"].append({"id":"footer","type":"footer","props":{"business_name":business,"public_contact":public_contact,"navigation":[],"copyright_text":business}});schema=SiteSchema.model_validate(site)
  p=await StudioService.create_project(db,tenant_id,user_id,business,description);v=await db.get(StudioVersion,p.active_draft_version_id);v.schema_json=schema.model_dump_json();await db.flush()
  context={"company_profile":profile,"planning_request":{"objective":"Get this business online","business_identity":business,"description":description,"products_services":services,"contact":contact,"brand":profile.get("brand",{}),"target_customers":profile.get("target_customers"),"service_areas":profile.get("service_areas")}}
  row=await self._record(db,tenant_id,RuntimeType.STUDIO,p.id,name=business,description=description,solution_type=SolutionType.DIGITAL_PRESENCE,lifecycle_status=LifecycleStatus.PREVIEW_READY,current_version_reference=v.id,preview_state="ready",preview_url="/api/solutions/{solution_id}/preview",production_state="offline",production_url=None,visibility="private",context_json=json.dumps(context,sort_keys=True));await append_event(db,tenant_id=tenant_id,event_type="solution.created",payload={"solution_id":row.id,"solution_type":row.solution_type,"status":row.lifecycle_status},source="solutions");return row
 async def approve(self,db,tenant_id,solution_id,user_id):
  from packages.solutions.production import ProductionService
  return await ProductionService(self).publish(db,tenant_id,solution_id,user_id)
