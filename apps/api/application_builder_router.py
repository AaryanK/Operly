import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.application_builder.catalog import component_catalog, module_catalog
from packages.application_builder.renderer import render_application
from packages.application_builder.schema import ApplicationManifest, ProposalRequest, RecordInput
from packages.application_builder.service import ApplicationBuilderService, BuilderError
from packages.database.application_builder_models import ApplicationChangeSet, ApplicationVersion, ManagedApplication, ManagedRecord

router=APIRouter(tags=["application-builder"]);service=ApplicationBuilderService()
def failure(error):return HTTPException(403 if isinstance(error,PermissionError) else 404 if isinstance(error,LookupError) else 422,str(error))
def change(row):return {"id":row.id,"applicationId":row.application_id,"baseVersionId":row.base_version_id,"scope":row.scope,"operations":json.loads(row.operations_json),"before":json.loads(row.before_json),"after":json.loads(row.after_json),"validation":json.loads(row.validation_json),"risk":row.risk,"status":row.status,"appliedVersionId":row.applied_version_id}

@router.get("/api/application-builder/catalog/modules")
async def modules(auth:AuthContext=Depends(get_auth_context)):return module_catalog()
@router.get("/api/application-builder/catalog/components")
async def components(auth:AuthContext=Depends(get_auth_context)):return component_catalog()
@router.get("/api/application-builder/applications")
async def applications(auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    rows=(await db.scalars(select(ManagedApplication).where(ManagedApplication.tenant_id==auth.tenant.id).order_by(desc(ManagedApplication.created_at)))).all();return [{"id":x.id,"name":x.name,"slug":x.slug,"description":x.description,"activeVersionId":x.active_version_id} for x in rows]
@router.post("/api/application-builder/applications")
async def create_application(payload:dict,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:app,version=await service.create(db,auth.tenant.id,auth.user.id,str(payload.get("name","") ),str(payload.get("description","")));return {"id":app.id,"name":app.name,"slug":app.slug,"activeVersionId":version.id,"versionNumber":1}
    except (BuilderError,PermissionError) as e:raise failure(e)
@router.get("/api/application-builder/applications/{application_id}")
async def get_application(application_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:app,version,manifest=await service.current(db,auth.tenant.id,application_id);return {"id":app.id,"name":app.name,"slug":app.slug,"activeVersionId":version.id,"versionNumber":version.version_number,"manifest":manifest.model_dump(mode="json")}
    except (BuilderError,LookupError) as e:raise failure(e)
@router.post("/api/application-builder/proposals")
async def propose(payload:ProposalRequest,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:return change(await service.propose(db,auth.tenant.id,auth.user.id,auth.role,payload))
    except (BuilderError,PermissionError,LookupError) as e:raise failure(e)
@router.get("/api/application-builder/change-sets/{change_id}")
async def get_change(change_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:return change(await service.change_set(db,auth.tenant.id,change_id))
    except LookupError as e:raise failure(e)
@router.post("/api/application-builder/change-sets/{change_id}/preview")
async def preview(change_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:p=await service.preview(db,auth.tenant.id,auth.user.id,change_id);return {"previewSessionId":p.id,"url":f"/apps/{p.application_id}/preview?changeSetId={change_id}","activeMutated":False}
    except (BuilderError,LookupError) as e:raise failure(e)
@router.post("/api/application-builder/change-sets/{change_id}/apply")
async def apply(change_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:v=await service.apply(db,auth.tenant.id,auth.user.id,auth.role,change_id);return {"ok":True,"versionId":v.id,"versionNumber":v.version_number}
    except (BuilderError,PermissionError,LookupError) as e:raise failure(e)
@router.get("/api/application-builder/applications/{application_id}/versions")
async def versions(application_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    await service.application(db,auth.tenant.id,application_id);rows=(await db.scalars(select(ApplicationVersion).where(ApplicationVersion.application_id==application_id,ApplicationVersion.tenant_id==auth.tenant.id).order_by(desc(ApplicationVersion.version_number)))).all();return [{"id":x.id,"versionNumber":x.version_number,"summary":x.summary,"active":x.active,"sourceVersionId":x.source_version_id} for x in rows]
@router.post("/api/application-builder/applications/{application_id}/versions/{version_id}/rollback")
async def rollback(application_id:str,version_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:v=await service.rollback(db,auth.tenant.id,auth.user.id,auth.role,application_id,version_id);return {"ok":True,"versionId":v.id,"versionNumber":v.version_number}
    except (BuilderError,PermissionError,LookupError) as e:raise failure(e)
@router.get("/apps/{application_id}/preview",response_class=HTMLResponse)
async def preview_app(application_id:str,changeSetId:str|None=None,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:
        app,version,manifest=await service.current(db,auth.tenant.id,application_id)
        if changeSetId:
            row=await service.change_set(db,auth.tenant.id,changeSetId)
            if row.application_id!=app.id:raise LookupError("Change set not found")
            manifest=ApplicationManifest.model_validate_json(row.after_json)
        return render_application(manifest,studio=True)
    except (BuilderError,LookupError) as e:raise failure(e)
@router.get("/apps/{application_id}/run",response_class=HTMLResponse)
async def run_app(application_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:app,version,manifest=await service.current(db,auth.tenant.id,application_id);return render_application(manifest)
    except (BuilderError,LookupError) as e:raise failure(e)
@router.post("/api/application-builder/applications/{application_id}/entities/{entity_id}/records")
async def create_record(application_id:str,entity_id:str,payload:RecordInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:
        app,version,manifest=await service.current(db,auth.tenant.id,application_id);entity=next((x for x in manifest.entities if x.id==entity_id),None)
        if not entity:raise LookupError("Entity not found")
        allowed={x.id:x for x in entity.fields};unknown=set(payload.data)-set(allowed)
        if unknown:raise BuilderError("Unknown managed fields")
        for field in entity.fields:
            if field.required and payload.data.get(field.id) in {None,""}:raise BuilderError(f"{field.name} is required")
        row=ManagedRecord(tenant_id=auth.tenant.id,application_id=app.id,entity_id=entity_id,data_json=json.dumps(payload.data),created_by=auth.user.id);db.add(row);await db.commit();return {"id":row.id,"data":payload.data}
    except (BuilderError,LookupError) as e:raise failure(e)
@router.get("/api/application-builder/applications/{application_id}/entities/{entity_id}/records")
async def records(application_id:str,entity_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    await service.current(db,auth.tenant.id,application_id);rows=(await db.scalars(select(ManagedRecord).where(ManagedRecord.tenant_id==auth.tenant.id,ManagedRecord.application_id==application_id,ManagedRecord.entity_id==entity_id))).all();return [{"id":x.id,"data":json.loads(x.data_json)} for x in rows]
