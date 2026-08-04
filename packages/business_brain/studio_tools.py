import json
from sqlalchemy import desc,select
from packages.database.db import session_scope
from packages.database.studio_models import StudioProject,StudioVersion,StudioDeployment
from packages.studio.ai import StudioAI
from packages.studio.schema import SiteSchema
from packages.studio.service import StudioService

svc=StudioService()
async def list_projects(ctx,args):
    async with session_scope() as db:
        rows=(await db.scalars(select(StudioProject).where(StudioProject.tenant_id==ctx.tenant_id,StudioProject.status!="archived").order_by(desc(StudioProject.updated_at)))).all()
        return {"ok":True,"projects":[{"id":p.id,"name":p.name,"status":p.status} for p in rows]}
async def create_project(ctx,args):
    async with session_scope() as db:p=await svc.create_project(db,ctx.tenant_id,ctx.principal_id,str(args.get("name", "Studio site"))[:200],str(args.get("description", ""))[:4000]);return {"ok":True,"project_id":p.id,"name":p.name,"published":False}
async def generate(ctx,args):
    async with session_scope() as db:
        p=await svc.project(db,ctx.tenant_id,str(args.get("project_id","")));schema=await StudioAI().generate(str(args.get("request","")));v=await svc.save_schema(db,ctx.tenant_id,p.id,ctx.principal_id,schema.model_dump(mode="json"),"AI agent generation");return {"ok":True,"project_id":p.id,"version_id":v.id,"published":False,"pages":[x.title for x in schema.pages]}
async def versions(ctx,args):
    async with session_scope() as db:
        p=await svc.project(db,ctx.tenant_id,str(args.get("project_id","")));rows=(await db.scalars(select(StudioVersion).where(StudioVersion.tenant_id==ctx.tenant_id,StudioVersion.project_id==p.id).order_by(desc(StudioVersion.version_number)))).all();return {"ok":True,"versions":[{"id":v.id,"number":v.version_number,"status":v.status} for v in rows]}
async def publish(ctx,args):
    if args.get("explicit_confirmation") is not True:return {"ok":False,"error":"Explicit publish confirmation is required"}
    async with session_scope() as db:
        p=await svc.project(db,ctx.tenant_id,str(args.get("project_id","")));vid=str(args.get("version_id") or p.active_draft_version_id);d,url=await svc.publish(db,ctx.tenant_id,p.id,vid,ctx.principal_id);return {"ok":True,"public_url":url}
async def public_url(ctx,args):
    async with session_scope() as db:
        p=await svc.project(db,ctx.tenant_id,str(args.get("project_id","")));d=await db.scalar(select(StudioDeployment).where(StudioDeployment.tenant_id==ctx.tenant_id,StudioDeployment.project_id==p.id,StudioDeployment.status=="active"));return {"ok":True,"public_slug":d.public_slug if d else None}
def schema(name,description,properties,required=None):return {"type":"function","function":{"name":name,"description":description,"parameters":{"type":"object","properties":properties,"required":required or [],"additionalProperties":False}}}
def register_studio_tools(registry):
    registry.register(schema("list_studio_projects","List Studio projects for the current tenant.",{}),list_projects)
    registry.register(schema("create_studio_project","Create an unpublished Studio project.",{"name":{"type":"string"},"description":{"type":"string"}},["name"]),create_project,risk="medium")
    registry.register(schema("generate_studio_site","Generate a validated draft, never publish it.",{"project_id":{"type":"string"},"request":{"type":"string"}},["project_id","request"]),generate,risk="medium")
    registry.register(schema("list_studio_versions","List project versions.",{"project_id":{"type":"string"}},["project_id"]),versions)
    registry.register(schema("publish_studio_version","Publish only after the user explicitly asks.",{"project_id":{"type":"string"},"version_id":{"type":"string"},"explicit_confirmation":{"type":"boolean"}},["project_id","explicit_confirmation"]),publish,risk="high")
    registry.register(schema("get_studio_public_url","Get the current deployment slug.",{"project_id":{"type":"string"}},["project_id"]),public_url)
