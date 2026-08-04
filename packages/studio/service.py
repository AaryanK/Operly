import json, os, re, secrets
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from packages.database.studio_models import StudioAuditEvent, StudioDeployment, StudioProject, StudioVersion
from packages.studio.schema import SiteSchema, blank_site

def slugify(value): return re.sub(r"[^a-z0-9]+","-",value.lower()).strip("-")[:80] or "site"
class StudioService:
    @staticmethod
    async def project(db:AsyncSession,tenant_id:str,project_id:str):
        row=await db.scalar(select(StudioProject).where(StudioProject.id==project_id,StudioProject.tenant_id==tenant_id))
        if not row: raise LookupError("Project not found")
        return row
    @classmethod
    async def create_project(cls,db,tenant_id,user_id,name,description=""):
        base=slugify(name); slug=base; n=1
        while await db.scalar(select(StudioProject.id).where(StudioProject.tenant_id==tenant_id,StudioProject.slug==slug)):
            n+=1; slug=f"{base}-{n}"
        p=StudioProject(tenant_id=tenant_id,name=name[:200],slug=slug,description=description[:4000],created_by=user_id); db.add(p); await db.flush()
        v=StudioVersion(tenant_id=tenant_id,project_id=p.id,version_number=1,schema_json=blank_site(name,description).model_dump_json(),change_summary="Initial draft",created_by=user_id)
        db.add(v); await db.flush(); p.active_draft_version_id=v.id
        db.add(StudioAuditEvent(tenant_id=tenant_id,project_id=p.id,version_id=v.id,actor_id=user_id,action="project_created")); await db.commit(); return p
    @classmethod
    async def save_schema(cls,db,tenant_id,project_id,user_id,data,summary="Draft saved"):
        p=await cls.project(db,tenant_id,project_id); schema=SiteSchema.model_validate(data)
        number=(await db.scalar(select(func.max(StudioVersion.version_number)).where(StudioVersion.project_id==p.id)) or 0)+1
        v=StudioVersion(tenant_id=tenant_id,project_id=p.id,version_number=number,schema_json=schema.model_dump_json(),change_summary=summary[:500],created_by=user_id)
        db.add(v); await db.flush(); p.active_draft_version_id=v.id; p.updated_at=datetime.utcnow(); db.add(StudioAuditEvent(tenant_id=tenant_id,project_id=p.id,version_id=v.id,actor_id=user_id,action="draft_saved")); await db.commit(); return v
    @classmethod
    async def version(cls,db,tenant_id,project_id,version_id):
        await cls.project(db,tenant_id,project_id); v=await db.scalar(select(StudioVersion).where(StudioVersion.id==version_id,StudioVersion.project_id==project_id,StudioVersion.tenant_id==tenant_id))
        if not v: raise LookupError("Version not found")
        return v
    @classmethod
    async def publish(cls,db,tenant_id,project_id,version_id,user_id):
        p=await cls.project(db,tenant_id,project_id); v=await cls.version(db,tenant_id,project_id,version_id); SiteSchema.model_validate_json(v.schema_json)
        if v.status!="draft": raise ValueError("Only a draft can be published")
        old=await db.scalar(select(StudioVersion).where(StudioVersion.id==p.published_version_id)) if p.published_version_id else None
        if old: old.status="superseded"
        v.status="published"; v.published_at=datetime.utcnow(); p.published_version_id=v.id; p.status="published"
        d=await db.scalar(select(StudioDeployment).where(StudioDeployment.project_id==p.id,StudioDeployment.tenant_id==tenant_id))
        if not d: d=StudioDeployment(tenant_id=tenant_id,project_id=p.id,version_id=v.id,public_slug=f"{p.slug}-{secrets.token_hex(4)}"); db.add(d)
        else: d.version_id=v.id; d.status="active"
        db.add(StudioAuditEvent(tenant_id=tenant_id,project_id=p.id,version_id=v.id,actor_id=user_id,action="version_published")); await db.commit()
        return d, f"{os.getenv('PUBLIC_BASE_URL','http://localhost:8000').rstrip('/')}/sites/{d.public_slug}"
    @classmethod
    async def rollback(cls,db,tenant_id,project_id,version_id,user_id):
        old=await cls.version(db,tenant_id,project_id,version_id)
        v=await cls.save_schema(db,tenant_id,project_id,user_id,json.loads(old.schema_json),f"Rollback from version {old.version_number}")
        db.add(StudioAuditEvent(tenant_id=tenant_id,project_id=project_id,version_id=v.id,actor_id=user_id,action="version_rolled_back",details_json=json.dumps({"source_version":old.id}))); await db.commit(); return v
