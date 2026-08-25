import json
import os
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlparse

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.company.events import append_event
from packages.company.intelligence import profile_payload
from packages.database.application_builder_models import ApplicationVersion, ManagedApplication
from packages.database.custom_software_models import GeneratedProject, GeneratedSourceBundle, RunnerBuildRecord, RunnerPreviewRecord
from packages.database.models import Tenant
from packages.database.product_models import CompanyProfile, SolutionDeployment, SolutionRecord
from packages.database.software_project_models import SoftwareProjectRecord, SoftwareSourceVersionRecord
from packages.database.studio_models import StudioDeployment, StudioProject, StudioVersion
from packages.database.studio_source_models import StudioSourceVersion
from packages.studio.service import StudioService


class LifecycleStatus(StrEnum):
    DRAFT="draft"; PLANNING="planning"; BUILDING="building"; PREVIEW_READY="preview_ready"; APPROVED="approved"; PUBLISHING="publishing"; LIVE="live"; DEGRADED="degraded"; FAILED="failed"; ARCHIVED="archived"
class RuntimeType(StrEnum):
    STUDIO="studio"; MANAGED_APP="managed_app"; GENERATED_PROJECT="generated_project"; SOFTWARE_PROJECT="software_project"
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
    for key in (
        "status","stage","jobId","attempt","changeSetId","versionId","bootstrapVersionId",
        "softwarePlanId","softwarePlanVersion","sourceBundleId","sourceVersion","canonicalSourceVersionId",
        "buildId","repairCount","deliveryStatus","sourceArchiveArtifactId",
    ):
        value=initial.get(key)
        if value is not None:result[key]=value
    if initial.get("error"):
        result["error"]=" ".join(str(initial.get("error")).split())[:1000]
    return result or None


def _approved_runner_preview_target(value: str) -> bool:
    try:
        parsed=urlparse(value)
        port=parsed.port
    except ValueError:
        return False
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        return False
    environment=os.getenv("OPERLY_ENV",os.getenv("APP_ENV","development")).strip().lower()
    local_test=(
        environment in {"test","development","dev"}
        and os.getenv("OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER","").strip()=="1"
        and parsed.hostname=="127.0.0.1"
        and parsed.scheme in {"http","https"}
    )
    if local_test:return True
    allowed={host.strip().lower() for host in os.getenv("OPERLY_SANDBOX_PREVIEW_HOSTS","").split(",") if host.strip()}
    return parsed.scheme=="https" and port in {None,443} and parsed.hostname.lower() in allowed


def solution_json(row):
    preview=row.preview_url.replace("{solution_id}",row.id) if row.preview_url else None
    runtime_kind={
        RuntimeType.STUDIO:"studio",
        RuntimeType.MANAGED_APP:"app",
        RuntimeType.GENERATED_PROJECT:"generated",
        RuntimeType.SOFTWARE_PROJECT:"software",
    }.get(row.runtime_type,"unknown")
    return {
        "id":row.id,"name":row.name,"description":row.description,"solution_type":row.solution_type,
        "status":row.lifecycle_status,"current_version":row.current_version_reference,
        "preview":{"state":row.preview_state,"url":preview},
        "production":{"state":row.production_state,"url":row.production_url},"visibility":row.visibility,
        "runtime":{"kind":runtime_kind,"id":row.runtime_reference},"generation":_generation_payload(row),
        "created_at":row.created_at.isoformat(),"updated_at":row.updated_at.isoformat(),
    }


class SolutionService:
    async def _record(self,db,tenant_id,runtime_type,runtime_reference,**values):
        row=await db.scalar(select(SolutionRecord).where(
            SolutionRecord.tenant_id==tenant_id,
            SolutionRecord.runtime_type==runtime_type,
            SolutionRecord.runtime_reference==runtime_reference,
        ))
        if not row:
            row=SolutionRecord(tenant_id=tenant_id,runtime_type=runtime_type,runtime_reference=runtime_reference,**values)
            db.add(row);await db.flush()
        else:
            for key,value in values.items():
                if key=="context_json" and value=="{}" and str(row.context_json or "{}").strip() not in {"","{}"}:continue
                setattr(row,key,value)
        return row

    async def create_software_solution(self,db,*,tenant_id:str,user_id:str,project:SoftwareProjectRecord,objective:str,context:dict|None=None):
        payload=dict(context or {})
        payload.setdefault("ownerIntent",{"objective":str(objective or project.description or "")[:12000]})
        payload.setdefault("softwareProjectId",project.id)
        payload.setdefault("sourceAuthority","software_source_versions")
        row=await self._record(
            db,tenant_id,RuntimeType.SOFTWARE_PROJECT,project.id,
            name=project.name,description=project.description or str(objective or "")[:4000],
            solution_type=SolutionType.CUSTOM_SOLUTION,lifecycle_status=LifecycleStatus.BUILDING,
            current_version_reference=project.active_source_version_id,preview_state="unavailable",preview_url=None,
            production_state="offline",production_url=None,visibility="private",
            context_json=json.dumps(payload,ensure_ascii=False,sort_keys=True,default=str),
        )
        await append_event(db,tenant_id=tenant_id,event_type="solution.created",payload={"solution_id":row.id,"solution_type":row.solution_type,"status":row.lifecycle_status,"software_project_id":project.id},source="solutions")
        return row

    async def active_generated_preview(self,db:AsyncSession,tenant_id:str,plan_id:str|None,plan_version:int|None):
        if not plan_id or not plan_version:return None
        preview=await db.scalar(
            select(RunnerPreviewRecord)
            .join(RunnerBuildRecord,RunnerPreviewRecord.build_id==RunnerBuildRecord.id)
            .join(GeneratedSourceBundle,RunnerBuildRecord.source_bundle_id==GeneratedSourceBundle.id)
            .where(
                RunnerPreviewRecord.tenant_id==tenant_id,RunnerPreviewRecord.state=="active",
                RunnerPreviewRecord.expires_at>datetime.utcnow(),RunnerBuildRecord.tenant_id==tenant_id,
                RunnerBuildRecord.plan_id==plan_id,RunnerBuildRecord.state=="preview_ready",
                GeneratedSourceBundle.tenant_id==tenant_id,GeneratedSourceBundle.plan_id==plan_id,
                GeneratedSourceBundle.plan_version==plan_version,
            ).order_by(desc(RunnerPreviewRecord.created_at)).limit(1)
        )
        if preview and not _approved_runner_preview_target(preview.target_url):return None
        return preview

    async def active_software_preview(self,db:AsyncSession,tenant_id:str,project_id:str):
        application_id=f"software-project-{project_id}"
        preview=await db.scalar(
            select(RunnerPreviewRecord)
            .join(RunnerBuildRecord,RunnerPreviewRecord.build_id==RunnerBuildRecord.id)
            .join(GeneratedSourceBundle,RunnerBuildRecord.source_bundle_id==GeneratedSourceBundle.id)
            .where(
                RunnerPreviewRecord.tenant_id==tenant_id,RunnerPreviewRecord.state=="active",
                RunnerPreviewRecord.expires_at>datetime.utcnow(),RunnerBuildRecord.tenant_id==tenant_id,
                RunnerBuildRecord.state=="preview_ready",GeneratedSourceBundle.tenant_id==tenant_id,
                GeneratedSourceBundle.application_id==application_id,
            ).order_by(desc(RunnerPreviewRecord.created_at)).limit(1)
        )
        if preview and not _approved_runner_preview_target(preview.target_url):return None
        return preview

    async def latest_generated_source(self,db:AsyncSession,tenant_id:str,plan_id:str|None,plan_version:int|None):
        if not plan_id or not plan_version:return None
        return await db.scalar(select(GeneratedSourceBundle).where(
            GeneratedSourceBundle.tenant_id==tenant_id,GeneratedSourceBundle.plan_id==plan_id,
            GeneratedSourceBundle.plan_version==plan_version,
        ).order_by(desc(GeneratedSourceBundle.source_version)).limit(1))

    async def sync(self,db:AsyncSession,tenant_id:str):
        # Canonical software Solutions are primary records. Sync only projects state
        # into them; never manufacture a legacy runtime or replace their identity.
        software_rows=(await db.scalars(select(SolutionRecord).where(
            SolutionRecord.tenant_id==tenant_id,SolutionRecord.runtime_type==RuntimeType.SOFTWARE_PROJECT,
        ))).all()
        for row in software_rows:
            project=await db.scalar(select(SoftwareProjectRecord).where(
                SoftwareProjectRecord.id==row.runtime_reference,SoftwareProjectRecord.tenant_id==tenant_id,
            ))
            if not project:continue
            preview=await self.active_software_preview(db,tenant_id,project.id)
            state=str(project.state or "draft")
            if preview:state=LifecycleStatus.PREVIEW_READY
            row.name=project.name;row.description=project.description
            row.current_version_reference=project.active_source_version_id
            if row.production_state=="live":row.lifecycle_status=LifecycleStatus.LIVE
            elif state in {item.value for item in LifecycleStatus}:row.lifecycle_status=state
            row.preview_state="ready" if preview else ("unavailable" if state in {"building","failed","draft"} else row.preview_state)
            row.preview_url="/api/solutions/{solution_id}/preview" if preview else (None if row.preview_state=="unavailable" else row.preview_url)

        studios=(await db.scalars(select(StudioProject).where(StudioProject.tenant_id==tenant_id))).all()
        for p in studios:
            legacy_deployment=await db.scalar(select(StudioDeployment).where(StudioDeployment.tenant_id==tenant_id,StudioDeployment.project_id==p.id,StudioDeployment.status=="active"))
            source=await db.scalar(select(StudioSourceVersion).where(StudioSourceVersion.tenant_id==tenant_id,StudioSourceVersion.project_id==p.id).order_by(desc(StudioSourceVersion.source_version)))
            existing=await db.scalar(select(SolutionRecord).where(SolutionRecord.tenant_id==tenant_id,SolutionRecord.runtime_type==RuntimeType.STUDIO,SolutionRecord.runtime_reference==p.id))
            production=await db.scalar(select(SolutionDeployment).where(SolutionDeployment.tenant_id==tenant_id,SolutionDeployment.solution_id==existing.id,SolutionDeployment.status=="active").order_by(desc(SolutionDeployment.deployed_at))) if existing else None
            live=bool(production or (p.published_version_id and legacy_deployment));ready=bool(source or p.active_draft_version_id)
            status=LifecycleStatus.ARCHIVED if p.status=="archived" else LifecycleStatus.LIVE if live else LifecycleStatus.PREVIEW_READY if ready else LifecycleStatus.DRAFT
            current=production.version_reference if production else source.id if source else p.published_version_id or p.active_draft_version_id
            preview_url=f"/api/studio/projects/{p.id}/source/preview/" if source else f"/api/solutions/{{solution_id}}/preview" if ready else None
            await self._record(db,tenant_id,RuntimeType.STUDIO,p.id,name=p.name,description=p.description,solution_type=existing.solution_type if existing else SolutionType.DIGITAL_PRESENCE,lifecycle_status=status,current_version_reference=current,preview_state="ready" if ready else "unavailable",preview_url=preview_url,production_state="live" if live else "offline",production_url=production.public_url if production else f"/sites/{legacy_deployment.public_slug}" if legacy_deployment else None,visibility="public" if live else "private",context_json=existing.context_json if existing else "{}")

        apps=(await db.scalars(select(ManagedApplication).where(ManagedApplication.tenant_id==tenant_id))).all()
        for app in apps:
            existing=await db.scalar(select(SolutionRecord).where(SolutionRecord.tenant_id==tenant_id,SolutionRecord.runtime_type==RuntimeType.MANAGED_APP,SolutionRecord.runtime_reference==app.id))
            active=await db.get(ApplicationVersion,app.active_version_id) if app.active_version_id else None
            bootstrap_only=bool(active and active.version_number==1 and active.summary=="Blank application");generated_ready=bool(active and not bootstrap_only)
            initial=(_context_payload(existing).get("initialGeneration") if existing else None) or {};generation_failed=isinstance(initial,dict) and initial.get("status") in {"retryable","failed"}
            lifecycle=LifecycleStatus.PREVIEW_READY if generated_ready else LifecycleStatus.FAILED if generation_failed else LifecycleStatus.DRAFT
            await self._record(db,tenant_id,RuntimeType.MANAGED_APP,app.id,name=app.name,description=app.description,solution_type=SolutionType.BUSINESS_APP,lifecycle_status=lifecycle,current_version_reference=active.id if generated_ready else None,preview_state="ready" if generated_ready else "unavailable",preview_url=f"/api/solutions/{{solution_id}}/preview" if generated_ready else None,production_state="offline",production_url=None,visibility="private",context_json=existing.context_json if existing else "{}")

        # Legacy generated projects remain visible only for migration/backward compatibility.
        projects=(await db.scalars(select(GeneratedProject).where(GeneratedProject.tenant_id==tenant_id))).all()
        for p in projects:
            existing=await db.scalar(select(SolutionRecord).where(SolutionRecord.tenant_id==tenant_id,SolutionRecord.runtime_type==RuntimeType.GENERATED_PROJECT,SolutionRecord.runtime_reference==p.id))
            preview=await self.active_generated_preview(db,tenant_id,p.plan_id,p.approved_plan_version);source=await self.latest_generated_source(db,tenant_id,p.plan_id,p.approved_plan_version)
            context=_context_payload(existing) if existing else {};initial=context.get("initialGeneration") if isinstance(context.get("initialGeneration"),dict) else None
            generation_failed=bool(initial and initial.get("status") in {"retryable","failed"});generation_building=bool(initial and initial.get("status") in {"pending","queued","running"});generation_verified=bool(initial and initial.get("status")=="applied")
            if preview:lifecycle=LifecycleStatus.PREVIEW_READY;current=str(source.source_version) if source else str(p.version);preview_state="ready";preview_url="/api/solutions/{solution_id}/preview"
            elif generation_failed:lifecycle=LifecycleStatus.FAILED;current=None;preview_state="unavailable";preview_url=None
            elif generation_building:lifecycle=LifecycleStatus.BUILDING;current=None;preview_state="unavailable";preview_url=None
            elif generation_verified:lifecycle=LifecycleStatus.APPROVED;current=str(source.source_version) if source else None;preview_state="unavailable";preview_url=None
            else:lifecycle=LifecycleStatus.APPROVED;current=str(source.source_version) if source else str(p.version);preview_state="available";preview_url="/api/solutions/{solution_id}/preview"
            await self._record(db,tenant_id,RuntimeType.GENERATED_PROJECT,p.id,name=existing.name if existing else p.name,description=existing.description if existing else p.prompt[:4000],solution_type=existing.solution_type if existing else SolutionType.CUSTOM_SOLUTION,lifecycle_status=lifecycle,current_version_reference=current,preview_state=preview_state,preview_url=preview_url,production_state=existing.production_state if existing else "offline",production_url=existing.production_url if existing else None,visibility=existing.visibility if existing else "private",context_json=existing.context_json if existing else "{}")
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
        models={RuntimeType.STUDIO:StudioProject,RuntimeType.MANAGED_APP:ManagedApplication,RuntimeType.GENERATED_PROJECT:GeneratedProject,RuntimeType.SOFTWARE_PROJECT:SoftwareProjectRecord}
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
        if runtime_type==RuntimeType.SOFTWARE_PROJECT:
            preview=await self.active_software_preview(db,tenant_id,runtime.id)
            if not preview:raise LookupError("Solution preview is not ready")
            return preview.target_url
        preview=await self.active_generated_preview(db,tenant_id,runtime.plan_id,runtime.approved_plan_version)
        if preview:return preview.target_url
        initial=_context_payload(row).get("initialGeneration")
        if isinstance(initial,dict):raise LookupError("Solution preview is not ready")
        return f"/api/custom-software/projects/{runtime.id}/preview"

    async def versions(self,db,tenant_id,solution_id):
        row,runtime=await self.resolve(db,tenant_id,solution_id)
        if row.runtime_type==RuntimeType.SOFTWARE_PROJECT:
            items=(await db.scalars(select(SoftwareSourceVersionRecord).where(
                SoftwareSourceVersionRecord.tenant_id==tenant_id,SoftwareSourceVersionRecord.project_id==runtime.id,
            ).order_by(desc(SoftwareSourceVersionRecord.source_version)))).all()
            return [{"id":x.id,"version":x.source_version,"kind":"source","status":"current" if x.id==runtime.active_source_version_id else "superseded","summary":x.change_summary,"created_at":x.created_at.isoformat()} for x in items]
        if row.runtime_type==RuntimeType.STUDIO:
            sources=(await db.scalars(select(StudioSourceVersion).where(StudioSourceVersion.tenant_id==tenant_id,StudioSourceVersion.project_id==runtime.id).order_by(desc(StudioSourceVersion.source_version)))).all()
            legacy=(await db.scalars(select(StudioVersion).where(StudioVersion.tenant_id==tenant_id,StudioVersion.project_id==runtime.id).order_by(desc(StudioVersion.version_number)))).all()
            output=[{"id":x.id,"version":x.source_version,"kind":"source","status":x.status,"summary":x.change_summary,"created_at":x.created_at.isoformat()} for x in sources]
            output.extend({"id":x.id,"version":x.version_number,"kind":"legacy_schema","status":x.status,"summary":x.change_summary,"created_at":x.created_at.isoformat()} for x in legacy);return output
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
        tenant=await db.get(Tenant,tenant_id);workspace_name=(tenant.name if tenant else "").strip()
        business=(name or profile.get("display_name") or profile.get("business_name") or profile.get("legal_name") or workspace_name or "Untitled Website").strip()[:200];description=str(profile.get("description") or "")[:500]
        p=await StudioService.create_project(db,tenant_id,user_id,business,description)
        context={"company_profile":profile,"source_engine":"studio_source_agent_v1","planning_request":{"objective":"Create the business website","business_identity":business,"description":description,"products_services":profile.get("products_services"),"contact":profile.get("contact"),"brand":profile.get("brand",{}),"target_customers":profile.get("target_customers"),"service_areas":profile.get("service_areas")}}
        row=await self._record(db,tenant_id,RuntimeType.STUDIO,p.id,name=business,description=description,solution_type=SolutionType.DIGITAL_PRESENCE,lifecycle_status=LifecycleStatus.PREVIEW_READY,current_version_reference=p.active_draft_version_id,preview_state="ready",preview_url="/api/solutions/{solution_id}/preview",production_state="offline",production_url=None,visibility="private",context_json=json.dumps(context,sort_keys=True,default=str))
        await append_event(db,tenant_id=tenant_id,event_type="solution.created",payload={"solution_id":row.id,"solution_type":row.solution_type,"status":row.lifecycle_status},source="solutions")
        return row

    async def approve(self,db,tenant_id,solution_id,user_id):
        from packages.solutions.production import ProductionService
        return await ProductionService(self).publish(db,tenant_id,solution_id,user_id)
