"""Canonical Solution registry for SoftwareProject-backed software."""
from __future__ import annotations

import json
import os
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlparse

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.company.events import append_event
from packages.database.custom_software_models import GeneratedSourceBundle, RunnerBuildRecord, RunnerPreviewRecord
from packages.database.product_models import SolutionRecord
from packages.database.software_project_models import SoftwareProjectRecord, SoftwareSourceVersionRecord


class LifecycleStatus(StrEnum):
    DRAFT="draft"; PLANNING="planning"; BUILDING="building"; PREVIEW_READY="preview_ready"; APPROVED="approved"; PUBLISHING="publishing"; LIVE="live"; DEGRADED="degraded"; FAILED="failed"; ARCHIVED="archived"


class RuntimeType(StrEnum):
    # Historical values remain parseable while old rows age out of the database, but
    # SolutionService never resolves or exposes them as product runtimes.
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
        "status","stage","jobId","attempt","softwarePlanId","softwarePlanVersion",
        "sourceBundleId","sourceVersion","canonicalSourceVersionId","buildId","repairCount",
        "deliveryStatus","sourceArchiveArtifactId",
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
    return {
        "id":row.id,
        "name":row.name,
        "description":row.description,
        "solution_type":row.solution_type,
        "status":row.lifecycle_status,
        "current_version":row.current_version_reference,
        "preview":{"state":row.preview_state,"url":preview},
        "production":{"state":row.production_state,"url":row.production_url},
        "visibility":row.visibility,
        "runtime":{"kind":"software","id":row.runtime_reference},
        "generation":_generation_payload(row),
        "created_at":row.created_at.isoformat(),
        "updated_at":row.updated_at.isoformat(),
    }


class SolutionService:
    async def _record(self,db,tenant_id,runtime_type,runtime_reference,**values):
        if RuntimeType(runtime_type) != RuntimeType.SOFTWARE_PROJECT:
            raise ValueError("SoftwareProject is the only supported Solution runtime")
        row=await db.scalar(select(SolutionRecord).where(
            SolutionRecord.tenant_id==tenant_id,
            SolutionRecord.runtime_type==RuntimeType.SOFTWARE_PROJECT,
            SolutionRecord.runtime_reference==runtime_reference,
        ))
        if not row:
            row=SolutionRecord(
                tenant_id=tenant_id,
                runtime_type=RuntimeType.SOFTWARE_PROJECT,
                runtime_reference=runtime_reference,
                **values,
            )
            db.add(row);await db.flush()
        else:
            for key,value in values.items():
                if key=="context_json" and value=="{}" and str(row.context_json or "{}").strip() not in {"","{}"}:continue
                setattr(row,key,value)
        return row

    async def create_software_solution(
        self,db,*,tenant_id:str,user_id:str,project:SoftwareProjectRecord,objective:str,context:dict|None=None
    ):
        payload=dict(context or {})
        payload.setdefault("ownerIntent",{"objective":str(objective or project.description or "")[:12000]})
        payload.setdefault("softwareProjectId",project.id)
        payload.setdefault("sourceAuthority","software_source_versions")
        row=await self._record(
            db,tenant_id,RuntimeType.SOFTWARE_PROJECT,project.id,
            name=project.name,
            description=project.description or str(objective or "")[:4000],
            solution_type=SolutionType.CUSTOM_SOLUTION,
            lifecycle_status=LifecycleStatus.BUILDING,
            current_version_reference=project.active_source_version_id,
            preview_state="unavailable",
            preview_url=None,
            production_state="offline",
            production_url=None,
            visibility="private",
            context_json=json.dumps(payload,ensure_ascii=False,sort_keys=True,default=str),
        )
        await append_event(
            db,
            tenant_id=tenant_id,
            event_type="solution.created",
            payload={
                "solution_id":row.id,
                "solution_type":row.solution_type,
                "status":row.lifecycle_status,
                "software_project_id":project.id,
            },
            source="solutions",
        )
        return row

    async def active_software_preview(self,db:AsyncSession,tenant_id:str,project_id:str):
        # GeneratedSourceBundle/RunnerBuildRecord are execution adapters only. Product
        # identity and source authority remain SoftwareProject/SoftwareSourceVersion.
        application_id=f"software-project-{project_id}"
        preview=await db.scalar(
            select(RunnerPreviewRecord)
            .join(RunnerBuildRecord,RunnerPreviewRecord.build_id==RunnerBuildRecord.id)
            .join(GeneratedSourceBundle,RunnerBuildRecord.source_bundle_id==GeneratedSourceBundle.id)
            .where(
                RunnerPreviewRecord.tenant_id==tenant_id,
                RunnerPreviewRecord.state=="active",
                RunnerPreviewRecord.expires_at>datetime.utcnow(),
                RunnerBuildRecord.tenant_id==tenant_id,
                RunnerBuildRecord.state=="preview_ready",
                GeneratedSourceBundle.tenant_id==tenant_id,
                GeneratedSourceBundle.application_id==application_id,
            )
            .order_by(desc(RunnerPreviewRecord.created_at))
            .limit(1)
        )
        if preview and not _approved_runner_preview_target(preview.target_url):return None
        return preview

    async def sync(self,db:AsyncSession,tenant_id:str):
        rows=(await db.scalars(select(SolutionRecord).where(
            SolutionRecord.tenant_id==tenant_id,
            SolutionRecord.runtime_type==RuntimeType.SOFTWARE_PROJECT,
        ))).all()
        for row in rows:
            project=await db.scalar(select(SoftwareProjectRecord).where(
                SoftwareProjectRecord.id==row.runtime_reference,
                SoftwareProjectRecord.tenant_id==tenant_id,
            ))
            if not project:
                row.lifecycle_status=LifecycleStatus.ARCHIVED
                continue
            preview=await self.active_software_preview(db,tenant_id,project.id)
            state=str(project.state or "draft")
            if preview:state=LifecycleStatus.PREVIEW_READY
            row.name=project.name
            row.description=project.description
            row.current_version_reference=project.active_source_version_id
            if row.production_state=="live":row.lifecycle_status=LifecycleStatus.LIVE
            elif state in {item.value for item in LifecycleStatus}:row.lifecycle_status=state
            row.preview_state="ready" if preview else ("unavailable" if state in {"building","failed","draft"} else row.preview_state)
            row.preview_url="/api/solutions/{solution_id}/preview" if preview else (None if row.preview_state=="unavailable" else row.preview_url)
        await db.flush()

    async def list(self,db,tenant_id):
        await self.sync(db,tenant_id)
        return (await db.scalars(
            select(SolutionRecord)
            .where(
                SolutionRecord.tenant_id==tenant_id,
                SolutionRecord.runtime_type==RuntimeType.SOFTWARE_PROJECT,
                SolutionRecord.lifecycle_status!=LifecycleStatus.ARCHIVED,
            )
            .order_by(desc(SolutionRecord.updated_at))
        )).all()

    async def get(self,db,tenant_id,solution_id):
        await self.sync(db,tenant_id)
        row=await db.scalar(select(SolutionRecord).where(
            SolutionRecord.id==solution_id,
            SolutionRecord.tenant_id==tenant_id,
            SolutionRecord.runtime_type==RuntimeType.SOFTWARE_PROJECT,
        ))
        if not row:raise LookupError("Solution not found")
        return row

    async def resolve(self,db,tenant_id,solution_id):
        row=await self.get(db,tenant_id,solution_id)
        runtime=await db.scalar(select(SoftwareProjectRecord).where(
            SoftwareProjectRecord.id==row.runtime_reference,
            SoftwareProjectRecord.tenant_id==tenant_id,
        ))
        if not runtime:raise LookupError("Software project not found")
        return row,runtime

    async def preview_target(self,db:AsyncSession,tenant_id:str,row,runtime)->str:
        if RuntimeType(row.runtime_type)!=RuntimeType.SOFTWARE_PROJECT:
            raise LookupError("Legacy Solution runtimes are retired")
        preview=await self.active_software_preview(db,tenant_id,runtime.id)
        if not preview:raise LookupError("Solution preview is not ready")
        return preview.target_url

    async def versions(self,db,tenant_id,solution_id):
        _,runtime=await self.resolve(db,tenant_id,solution_id)
        items=(await db.scalars(select(SoftwareSourceVersionRecord).where(
            SoftwareSourceVersionRecord.tenant_id==tenant_id,
            SoftwareSourceVersionRecord.project_id==runtime.id,
        ).order_by(desc(SoftwareSourceVersionRecord.source_version)))).all()
        return [
            {
                "id":item.id,
                "version":item.source_version,
                "kind":"source",
                "status":"current" if item.id==runtime.active_source_version_id else "superseded",
                "summary":item.change_summary,
                "created_at":item.created_at.isoformat(),
            }
            for item in items
        ]

    async def approve(self,db,tenant_id,solution_id,user_id):
        from packages.solutions.production import ProductionService
        return await ProductionService(self).publish(db,tenant_id,solution_id,user_id)
