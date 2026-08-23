import json
from datetime import datetime
from enum import StrEnum

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.company.events import append_event
from packages.company.intelligence import profile_payload
from packages.database.application_builder_models import ApplicationVersion, ManagedApplication
from packages.database.custom_software_models import GeneratedProject, GeneratedSourceBundle, RunnerBuildRecord, RunnerPreviewRecord
from packages.database.models import Tenant
from packages.database.product_models import CompanyProfile, SolutionDeployment, SolutionRecord
from packages.database.studio_models import StudioDeployment, StudioProject, StudioVersion
from packages.database.studio_source_models import StudioSourceVersion
from packages.studio.service import StudioService


class LifecycleStatus(StrEnum):
    DRAFT="draft"; PLANNING="planning"; BUILDING="building"; PREVIEW_READY="preview_ready"; APPROVED="approved"; PUBLISHING="publishing"; LIVE="live"; DEGRADED="degraded"; FAILED="failed"; ARCHIVED="archived"
class RuntimeType(StrEnum):
    STUDIO="studio"; MANAGED_APP="managed_app"; GENERATED_PROJECT="generated_project"
class SolutionType(StrEnum):
    DIGITAL_PRESENCE="digital_presence"; BUSINESS_APP="business_app"; CUSTOM_SOLUTION="custom_solution"


def _context_payload(row) -> dict:
    try:
        value=json.loads(row.context_json or "{}")
    except Exception:
        return {}
    return value if isinstance(value,dict) else {}


def _generation_payload(row):
    initial=_context_payload(row).get("initialGeneration")
    if not isinstance(initial,dict):return None
    result={}
    for key in ("status","stage","jobId","attempt","changeSetId","versionId","bootstrapVersionId"):
        value=initial.get(key)
        if value is not None:result[key]=value
    if initial.get("error"):
        result["error"]=" ".join(str(initial.get("error")).split())[:1000]
    return result or None


def solution_json(row):
    preview=row.preview_url.replace("{solution_id}",row.id) if row.preview_url else None
    runtime_kind={RuntimeType.STUDIO:"studio",RuntimeType.MANAGED_APP:"app",RuntimeType.GENERATED_PROJECT:"generated"}.get(row.runtime_type,"unknown")
    return {"id":row.id,"name":row.name,"description":row.description,"solution_type":row.solution_type,"status":row.lifecycle_status,"current_version":row.current_version_reference,"preview":{"state":row.preview_state,"url":preview},"production":{"state":row.production_state,"url":row.production_url},"visibility":row.visibility,"runtime":{"kind":runtime_kind,"id":row.runtime_reference},"generation":_generation_payload(row),"created_at":row.created_at.isoformat(),"updated_at":row.updated_at.isoformat()}


class SolutionService:
    async def _record(self,db,tenant_id,runtime_type,runtime_reference,**values):
        row=await db.scalar(select(SolutionRecord).where(SolutionRecord.tenant_id==tenant_id,SolutionRecord.runtime_type==runtime_type,SolutionRecord.runtime_reference==runtime_reference))
        if not row:
            row=SolutionRecord(tenant_id=tenant_id,runtime_type=runtime_type,runtime_reference=runtime_reference,**values);db.add(row);await db.flush()
        else:
            for key,value in values.items():
                if key=="context_json" and value=="{}" and str(row.context_json or "{}").strip() not in {"", "{}"}:
                    continue
                setattr(row,key,value)
        return row

    async def active_generated_preview(self,db:AsyncSession,tenant_id:str,plan_id:str|None,plan_version:int|None):
        if not plan_id or not plan_version:return None
        return await db.scalar(
            select(RunnerPreviewRecord)
            .join(RunnerBuildRecord,RunnerPreviewRecord.build_id==RunnerBuildRecord.id)
            .join(GeneratedSourceBundle,RunnerBuildRecord.source_bundle_id==GeneratedSourceBundle.id)
            .where(
                RunnerPreviewRecord.tenant_id==tenant_id,
                RunnerPreviewRecord.state=="active",
                RunnerPreviewRecord.expires_at>datetime.utcnow(),
                RunnerBuildRecord.tenant_id==tenant_id,
                RunnerBuildRecord.plan_id==plan_id,
                RunnerBuildRecord.state=="preview_ready",
                GeneratedSourceBundle.tenant_id==tenant_id,
                GeneratedSourceBundle.plan_id==plan_id,
                GeneratedSourceBundle.plan_version==plan_version,
            )
            .order_by(desc(RunnerPreviewRecord.created_at))
            .limit(1)
        )

    async def latest_generated_source(self,db:AsyncSession,tenant_id:str,plan_id:str|None,plan_version:int|None):
        if not plan_id or not plan_version:return None
        return await db.scalar(
            select(GeneratedSourceBundle)
            .where(
                GeneratedSourceBundle.tenant_id==tenant_id,
                GeneratedSourceBundle.plan_id==plan_id,
                GeneratedSourceBundle.plan_version==plan_version,
            )
            .order_by(desc(GeneratedSourceBundle.source_version))
            .limit(1)
        )

    async def sync(self,db:AsyncSession,tenant_id:str):
        studios=(await db.scalars(select(StudioProject).where(StudioProject.tenant_id==tenant_id))).all()
        for p in studios:
            legacy_deployment=await db.scalar(select(StudioDeployment).where(StudioDeployment.tenant_id==tenant_id,StudioDeployment.project_id==p.id,StudioDeployment.status=="active"))
            source=await db.scalar(select(StudioSourceVersion).where(StudioSourceVersion.tenant_id==tenant_id,StudioSourceVersion.project_id==p.id).order_by(desc(StudioSourceVersion.source_version)))
            existing=await db.scalar(select(SolutionRecord).where(SolutionRecord.tenant_id==tenant_id,SolutionRecord.runtime_type==RuntimeType.STUDIO,SolutionRecord.runtime_reference==p.id))
            production=await db.scalar(select(SolutionDeployment).where(SolutionDeployment.tenant_id==tenant_id,SolutionDeployment.solution_id==existing.id,SolutionDeployment.status=="active").order_by(desc(SolutionDeployment.deployed_at))) if existing else None
            live=bool(production or (p.published_version_id and legacy_deployment))
            ready=bool(source or p.active_draft_version_id)
            status=LifecycleStatus.ARCHIVED if p.status=="archived" else LifecycleStatus.LIVE if live else LifecycleStatus.PREVIEW_READY if ready else LifecycleStatus.DRAFT
            current=production.version_reference if production else source.id if source else p.published_version_id or p.active_draft_version_id
            preview_url=f"/api/studio/projects/{p.id}/source/preview/" if source else f"/api/solutions/{{solution_id}}/preview" if ready else None
            await self._record(db,tenant_id,RuntimeType.STUDIO,p.id,name=p.name,description=p.description,solution_type=existing.solution_type if existing else SolutionType.DIGITAL_PRESENCE,lifecycle_status=status,current_version_reference=current,preview_state="ready" if ready else "unavailable",preview_url=preview_url,production_state="live" if live else "offline",production_url=production.public_url if production else f"/sites/{legacy_deployment.public_slug}" if legacy_deployment else None,visibility="public" if live else "private",context_json=existing.context_json if existing else "{}")

        apps=(await db.scalars(select(ManagedApplication).where(ManagedApplication.tenant_id==tenant_id))).all()
        for app in apps:
            existing=await db.scalar(select(SolutionRecord).where(SolutionRecord.tenant_id==tenant_id,SolutionRecord.runtime_type==RuntimeType.MANAGED_APP,SolutionRecord.runtime_reference==app.id))
            active=await db.get(ApplicationVersion,app.active_version_id) if app.active_version_id else None
            # ApplicationBuilderService.create() persists a specifically labeled
            # blank bootstrap v1 so the editor has a schema to target. That
            # bootstrap is runtime state, not evidence that owner-requested
            # generation succeeded. Preserve genuinely generated/legacy v1
            # records that are not the canonical blank bootstrap.
            bootstrap_only=bool(active and active.version_number==1 and active.summary=="Blank application")
            generated_ready=bool(active and not bootstrap_only)
            initial=(_context_payload(existing).get("initialGeneration") if existing else None) or {}
            generation_failed=isinstance(initial,dict) and initial.get("status") in {"retryable","failed"}
            lifecycle=LifecycleStatus.PREVIEW_READY if generated_ready else LifecycleStatus.FAILED if generation_failed else LifecycleStatus.DRAFT
            await self._record(db,tenant_id,RuntimeType.MANAGED_APP,app.id,name=app.name,description=app.description,solution_type=SolutionType.BUSINESS_APP,lifecycle_status=lifecycle,current_version_reference=active.id if generated_ready else None,preview_state="ready" if generated_ready else "unavailable",preview_url=f"/api/solutions/{{solution_id}}/preview" if generated_ready else None,production_state="offline",production_url=None,visibility="private",context_json=existing.context_json if existing else "{}")

        projects=(await db.scalars(select(GeneratedProject).where(GeneratedProject.tenant_id==tenant_id))).all()
        for p in projects:
            preview=await self.active_generated_preview(db,tenant_id,p.plan_id,p.approved_plan_version)
            source=await self.latest_generated_source(db,tenant_id,p.plan_id,p.approved_plan_version)
            current=str(source.source_version) if source else str(p.version)
            await self._record(db,tenant_id,RuntimeType.GENERATED_PROJECT,p.id,name=p.name,description=p.prompt[:4000],solution_type=SolutionType.CUSTOM_SOLUTION,lifecycle_status=LifecycleStatus.PREVIEW_READY if preview else LifecycleStatus.APPROVED,current_version_reference=current,preview_state="ready" if preview else "available",preview_url=f"/api/solutions/{{solution_id}}/preview",production_state="offline",production_url=None,visibility="private",context_json="{}")
        await db.flush()

    async def list(self,db,tenant_id):
        await self.sync(db,tenant_id)
        return (await db.scalars(select(SolutionRecord).where(SolutionRecord.tenant_id==tenant_id,SolutionRecord.lifecycle_status!=LifecycleStatus.ARCHIVED).order_by(desc(SolutionRecord.updated_at)))).all()

    async def get(self,db,tenant_id,solution_id):
        await self.sync(db,tenant_id)
        row=await db.scalar(select(SolutionRecord).where(SolutionRecord.id==solution_id,SolutionRecord.tenant_id==tenant_id))
        if not row:raise LookupError("Solution not found")
        return row

    async def resolve(self,db,tenant_id,solution_id):
        row=await self.get(db,tenant_id,solution_id)
        models={RuntimeType.STUDIO:StudioProject,RuntimeType.MANAGED_APP:ManagedApplication,RuntimeType.GENERATED_PROJECT:GeneratedProject}
        model=models[RuntimeType(row.runtime_type)]
        runtime=await db.scalar(select(model).where(model.id==row.runtime_reference,model.tenant_id==tenant_id))
        if not runtime:raise LookupError("Solution runtime not found")
        return row,runtime

    async def preview_target(self,db:AsyncSession,tenant_id:str,row,runtime)->str:
        runtime_type=RuntimeType(row.runtime_type)
        if runtime_type==RuntimeType.STUDIO:return f"/api/studio/projects/{runtime.id}/preview"
        if runtime_type==RuntimeType.MANAGED_APP:
            if row.preview_state!="ready":raise LookupError("Solution preview is not ready")
            return f"/apps/{runtime.id}/preview"
        preview=await self.active_generated_preview(db,tenant_id,runtime.plan_id,runtime.approved_plan_version)
        if preview:return f"/api/custom-software/previews/{preview.id}/"
        return f"/api/custom-software/projects/{runtime.id}/preview"

    async def versions(self,db,tenant_id,solution_id):
        row,runtime=await self.resolve(db,tenant_id,solution_id)
        if row.runtime_type==RuntimeType.STUDIO:
            sources=(await db.scalars(select(StudioSourceVersion).where(StudioSourceVersion.tenant_id==tenant_id,StudioSourceVersion.project_id==runtime.id).order_by(desc(StudioSourceVersion.source_version)))).all()
            legacy=(await db.scalars(select(StudioVersion).where(StudioVersion.tenant_id==tenant_id,StudioVersion.project_id==runtime.id).order_by(desc(StudioVersion.version_number)))).all()
            output=[{"id":x.id,"version":x.source_version,"kind":"source","status":x.status,"summary":x.change_summary,"created_at":x.created_at.isoformat()} for x in sources]
            output.extend({"id":x.id,"version":x.version_number,"kind":"legacy_schema","status":x.status,"summary":x.change_summary,"created_at":x.created_at.isoformat()} for x in legacy)
            return output
        if row.runtime_type==RuntimeType.MANAGED_APP:
            items=(await db.scalars(select(ApplicationVersion).where(ApplicationVersion.tenant_id==tenant_id,ApplicationVersion.application_id==runtime.id).order_by(desc(ApplicationVersion.version_number)))).all()
            return [{"id":x.id,"version":x.version_number,"status":"active" if x.active else "superseded","summary":x.summary,"created_at":x.created_at.isoformat()} for x in items]
        if runtime.plan_id and runtime.approved_plan_version:
            sources=(await db.scalars(select(GeneratedSourceBundle).where(GeneratedSourceBundle.tenant_id==tenant_id,GeneratedSourceBundle.plan_id==runtime.plan_id,GeneratedSourceBundle.plan_version==runtime.approved_plan_version).order_by(desc(GeneratedSourceBundle.source_version)))).all()
            if sources:
                output=[]
                for index,item in enumerate(sources):
                    try:provenance=json.loads(item.provenance_json or "{}")
                    except Exception:provenance={}
                    output.append({"id":item.id,"version":item.source_version,"kind":"source","status":"current" if index==0 else "superseded","summary":provenance.get("summary") or provenance.get("sourceOperation") or "Generated source","created_at":item.created_at.isoformat()})
                return output
        return [{"id":f"{runtime.id}:{runtime.version}","version":runtime.version,"status":"current","summary":"Current generated version","created_at":runtime.created_at.isoformat()}]

    async def create_presence(self,db,tenant_id,user_id,name=None):
        profile=profile_payload(await db.get(CompanyProfile,tenant_id))["profile"] or {}
        existing=await db.scalar(select(SolutionRecord).where(SolutionRecord.tenant_id==tenant_id,SolutionRecord.solution_type==SolutionType.DIGITAL_PRESENCE,SolutionRecord.lifecycle_status!=LifecycleStatus.ARCHIVED))
        if existing:return await self.get(db,tenant_id,existing.id)
        tenant=await db.get(Tenant,tenant_id)
        workspace_name=(tenant.name if tenant else "").strip()
        business=(name or profile.get("display_name") or profile.get("business_name") or profile.get("legal_name") or workspace_name or "Untitled Website").strip()[:200]
        description=str(profile.get("description") or "")[:500]
        # Preserve one tiny legacy compatibility snapshot so old routes and
        # rollback remain safe. The source agent becomes primary immediately.
        p=await StudioService.create_project(db,tenant_id,user_id,business,description)
        context={"company_profile":profile,"source_engine":"studio_source_agent_v1","planning_request":{"objective":"Create the business website","business_identity":business,"description":description,"products_services":profile.get("products_services"),"contact":profile.get("contact"),"brand":profile.get("brand",{}),"target_customers":profile.get("target_customers"),"service_areas":profile.get("service_areas")}}
        row=await self._record(db,tenant_id,RuntimeType.STUDIO,p.id,name=business,description=description,solution_type=SolutionType.DIGITAL_PRESENCE,lifecycle_status=LifecycleStatus.PREVIEW_READY,current_version_reference=p.active_draft_version_id,preview_state="ready",preview_url="/api/solutions/{solution_id}/preview",production_state="offline",production_url=None,visibility="private",context_json=json.dumps(context,sort_keys=True,default=str))
        await append_event(db,tenant_id=tenant_id,event_type="solution.created",payload={"solution_id":row.id,"solution_type":row.solution_type,"status":row.lifecycle_status},source="solutions")
        return row

    async def approve(self,db,tenant_id,solution_id,user_id):
        from packages.solutions.production import ProductionService
        return await ProductionService(self).publish(db,tenant_id,solution_id,user_id)