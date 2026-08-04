import csv, hashlib, io, json, os, re, secrets, time
from pathlib import Path
from urllib.parse import parse_qs
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from apps.api.dependencies import AuthContext,get_auth_context,get_db
from packages.database.business_models import ActivityEvent,Contact,Lead
from packages.database.studio_models import *
from packages.studio.renderer import render_site
from packages.studio.schema import SiteSchema
from packages.studio.service import StudioService
from packages.studio.ai import StudioAI

router=APIRouter(tags=["studio"]); service=StudioService()
class ProjectInput(BaseModel): name:str=Field(min_length=1,max_length=200); description:str=""
class SchemaInput(BaseModel): schema_data:dict; change_summary:str="Draft saved"
class FormInput(BaseModel): name:str; form_key:str=Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$"); destination:str="store_only"; lead_title_template:str="Website inquiry"; initial_stage:str="new"; fields:list[dict]=[]
class AIInput(BaseModel): request:str=Field(min_length=1,max_length=12000)
def project_out(p): return {"id":p.id,"name":p.name,"slug":p.slug,"description":p.description,"status":p.status,"active_draft_version_id":p.active_draft_version_id,"published_version_id":p.published_version_id,"updated_at":p.updated_at.isoformat()}
@router.get("/api/studio/projects")
async def projects(auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    return [project_out(x) for x in (await db.scalars(select(StudioProject).where(StudioProject.tenant_id==auth.tenant.id,StudioProject.status!="archived").order_by(desc(StudioProject.updated_at)))).all()]
@router.post("/api/studio/projects")
async def create_project(x:ProjectInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)): return project_out(await service.create_project(db,auth.tenant.id,auth.user.id,x.name,x.description))
@router.get("/api/studio/projects/{pid}")
async def get_project(pid:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:p=await service.project(db,auth.tenant.id,pid)
    except LookupError:raise HTTPException(404,"Project not found")
    return project_out(p)
@router.patch("/api/studio/projects/{pid}")
async def update_project(pid:str,x:ProjectInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:p=await service.project(db,auth.tenant.id,pid)
    except LookupError:raise HTTPException(404,"Project not found")
    p.name=x.name;p.description=x.description[:4000];await db.commit();return project_out(p)
@router.post("/api/studio/projects/{pid}/archive")
async def archive(pid:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:p=await service.project(db,auth.tenant.id,pid)
    except LookupError:raise HTTPException(404,"Project not found")
    p.status="archived";await db.commit();return {"ok":True}
@router.get("/api/studio/projects/{pid}/versions")
async def versions(pid:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:await service.project(db,auth.tenant.id,pid)
    except LookupError:raise HTTPException(404,"Project not found")
    rows=(await db.scalars(select(StudioVersion).where(StudioVersion.tenant_id==auth.tenant.id,StudioVersion.project_id==pid).order_by(desc(StudioVersion.version_number)))).all()
    return [{"id":v.id,"version_number":v.version_number,"status":v.status,"change_summary":v.change_summary,"created_at":v.created_at.isoformat()} for v in rows]
@router.get("/api/studio/projects/{pid}/versions/{vid}")
async def version(pid:str,vid:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:v=await service.version(db,auth.tenant.id,pid,vid)
    except LookupError:raise HTTPException(404,"Version not found")
    return {"id":v.id,"version_number":v.version_number,"status":v.status,"schema":json.loads(v.schema_json)}
@router.post("/api/studio/projects/{pid}/versions")
@router.put("/api/studio/projects/{pid}/versions/{ignored_vid}")
async def save_version(pid:str,x:SchemaInput,ignored_vid:str|None=None,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:v=await service.save_schema(db,auth.tenant.id,pid,auth.user.id,x.schema_data,x.change_summary)
    except LookupError:raise HTTPException(404,"Project not found")
    except ValueError as e:raise HTTPException(422,str(e))
    return {"id":v.id,"version_number":v.version_number}
@router.post("/api/studio/projects/{pid}/versions/{vid}/publish")
async def publish(pid:str,vid:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:d,url=await service.publish(db,auth.tenant.id,pid,vid,auth.user.id)
    except LookupError:raise HTTPException(404,"Version not found")
    except ValueError as e:raise HTTPException(409,str(e))
    return {"ok":True,"public_slug":d.public_slug,"public_url":url}
@router.post("/api/studio/projects/{pid}/versions/{vid}/rollback")
async def rollback(pid:str,vid:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:v=await service.rollback(db,auth.tenant.id,pid,vid,auth.user.id)
    except LookupError:raise HTTPException(404,"Version not found")
    return {"id":v.id,"version_number":v.version_number}
async def preview_data(db,auth,pid,vid=None):
    p=await service.project(db,auth.tenant.id,pid); vid=vid or p.active_draft_version_id; v=await service.version(db,auth.tenant.id,pid,vid); return p,v
@router.get("/api/studio/projects/{pid}/preview",response_class=HTMLResponse)
@router.get("/api/studio/projects/{pid}/versions/{vid}/preview",response_class=HTMLResponse)
async def preview(pid:str,vid:str|None=None,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:p,v=await preview_data(db,auth,pid,vid); schema=SiteSchema.model_validate_json(v.schema_json); html=render_site(schema,schema.pages[0].slug,"preview")
    except LookupError:raise HTTPException(404,"Preview not found")
    return HTMLResponse(html,headers={"Content-Security-Policy":"default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' https:; form-action 'none'; frame-ancestors 'self'","Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})
@router.get("/api/studio/projects/{pid}/forms")
async def forms(pid:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:await service.project(db,auth.tenant.id,pid)
    except LookupError:raise HTTPException(404,"Project not found")
    return [{"id":f.id,"name":f.name,"form_key":f.form_key,"destination":f.destination,"fields":json.loads(f.schema_json)} for f in (await db.scalars(select(StudioForm).where(StudioForm.tenant_id==auth.tenant.id,StudioForm.project_id==pid))).all()]
@router.post("/api/studio/projects/{pid}/forms")
async def create_form(pid:str,x:FormInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:await service.project(db,auth.tenant.id,pid)
    except LookupError:raise HTTPException(404,"Project not found")
    if x.destination not in {"store_only","create_contact","create_lead","create_contact_and_lead"}:raise HTTPException(422,"Invalid destination")
    allowed={"text","email","tel","number","date","textarea"}
    for f in x.fields:
        if set(f)-{"key","label","type","required"} or f.get("type") not in allowed or not re.fullmatch(r"[a-z][a-z0-9_]{0,49}",str(f.get("key",""))):raise HTTPException(422,"Invalid form field")
    row=StudioForm(tenant_id=auth.tenant.id,project_id=pid,name=x.name[:200],form_key=x.form_key,destination=x.destination,lead_title_template=x.lead_title_template[:300],initial_stage=x.initial_stage[:50],schema_json=json.dumps(x.fields));db.add(row);await db.commit();return {"id":row.id}
@router.patch("/api/studio/projects/{pid}/forms/{fid}")
async def update_form(pid:str,fid:str,x:FormInput,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    row=await db.scalar(select(StudioForm).where(StudioForm.id==fid,StudioForm.project_id==pid,StudioForm.tenant_id==auth.tenant.id))
    if not row:raise HTTPException(404,"Form not found")
    if x.destination not in {"store_only","create_contact","create_lead","create_contact_and_lead"}:raise HTTPException(422,"Invalid destination")
    allowed={"text","email","tel","number","date","textarea"}
    for f in x.fields:
        if set(f)-{"key","label","type","required"} or f.get("type") not in allowed or not re.fullmatch(r"[a-z][a-z0-9_]{0,49}",str(f.get("key",""))):raise HTTPException(422,"Invalid form field")
    row.name=x.name[:200];row.form_key=x.form_key;row.destination=x.destination;row.lead_title_template=x.lead_title_template[:300];row.initial_stage=x.initial_stage[:50];row.schema_json=json.dumps(x.fields);await db.commit();return {"id":row.id}
@router.get("/api/studio/projects/{pid}/submissions")
async def submissions(pid:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:await service.project(db,auth.tenant.id,pid)
    except LookupError:raise HTTPException(404,"Project not found")
    return [{"id":x.id,"email":("***@"+x.normalized_email.split("@")[-1]) if x.normalized_email else None,"phone":("***"+x.normalized_phone[-4:]) if x.normalized_phone else None,"status":x.status,"page":x.public_page_slug,"created_at":x.created_at.isoformat()} for x in (await db.scalars(select(StudioFormSubmission).where(StudioFormSubmission.tenant_id==auth.tenant.id,StudioFormSubmission.project_id==pid).order_by(desc(StudioFormSubmission.created_at)))).all()]
@router.get("/api/studio/projects/{pid}/submissions/{sid}")
async def submission(pid:str,sid:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    row=await db.scalar(select(StudioFormSubmission).where(StudioFormSubmission.id==sid,StudioFormSubmission.project_id==pid,StudioFormSubmission.tenant_id==auth.tenant.id))
    if not row:raise HTTPException(404,"Submission not found")
    return {"id":row.id,"payload":json.loads(row.payload_json),"status":row.status,"created_at":row.created_at.isoformat()}
@router.get("/api/studio/projects/{pid}/analytics")
async def analytics(pid:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:await service.project(db,auth.tenant.id,pid)
    except LookupError:raise HTTPException(404,"Project not found")
    views=await db.scalar(select(func.count(StudioPageView.id)).where(StudioPageView.tenant_id==auth.tenant.id,StudioPageView.project_id==pid)) or 0; subs=await db.scalar(select(func.count(StudioFormSubmission.id)).where(StudioFormSubmission.tenant_id==auth.tenant.id,StudioFormSubmission.project_id==pid)) or 0
    return {"page_views":views,"form_submissions":subs,"conversion_rate":round(subs/views*100,1) if views else 0}

ASSET_ROOT=Path(os.getenv("STUDIO_ASSET_DIR",Path(__file__).resolve().parents[2]/"studio_assets")).resolve()
def image_type(data:bytes):
    if data.startswith(b"\xff\xd8\xff"):return "image/jpeg","jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):return "image/png","png"
    if data[:4]==b"RIFF" and data[8:12]==b"WEBP":return "image/webp","webp"
    return None
@router.get("/api/studio/projects/{pid}/assets")
async def assets(pid:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:await service.project(db,auth.tenant.id,pid)
    except LookupError:raise HTTPException(404,"Project not found")
    return [{"id":x.id,"public_asset_key":x.public_asset_key,"original_filename":x.original_filename,"content_type":x.content_type,"size_bytes":x.size_bytes,"url":f"/public-assets/{x.public_asset_key}"} for x in (await db.scalars(select(StudioAsset).where(StudioAsset.tenant_id==auth.tenant.id,StudioAsset.project_id==pid))).all()]
@router.post("/api/studio/projects/{pid}/assets")
async def upload_asset(pid:str,request:Request,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:await service.project(db,auth.tenant.id,pid)
    except LookupError:raise HTTPException(404,"Project not found")
    data=await request.body(); filename=Path(request.headers.get("x-file-name","image")).name[:255]
    if len(data)>5*1024*1024:raise HTTPException(413,"Image too large")
    detected=image_type(data); declared=request.headers.get("content-type","").split(";",1)[0]
    if not detected or declared!=detected[0]:raise HTTPException(415,"Only matching JPEG, PNG, and WebP images are accepted")
    count=await db.scalar(select(func.count(StudioAsset.id)).where(StudioAsset.tenant_id==auth.tenant.id,StudioAsset.project_id==pid)) or 0
    if count>=100:raise HTTPException(409,"Asset limit reached")
    key=secrets.token_urlsafe(18); stored=f"{secrets.token_hex(20)}.{detected[1]}"; folder=(ASSET_ROOT/auth.tenant.id/pid).resolve()
    if ASSET_ROOT not in folder.parents:raise HTTPException(400,"Invalid asset path")
    folder.mkdir(parents=True,exist_ok=True); path=folder/stored; path.write_bytes(data)
    row=StudioAsset(tenant_id=auth.tenant.id,project_id=pid,public_asset_key=key,original_filename=filename,stored_filename=stored,content_type=detected[0],size_bytes=len(data),storage_path=str(path),created_by=auth.user.id);db.add(row);await db.commit();return {"id":row.id,"public_asset_key":key,"url":f"/public-assets/{key}"}
@router.delete("/api/studio/projects/{pid}/assets/{aid}")
async def delete_asset(pid:str,aid:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    row=await db.scalar(select(StudioAsset).where(StudioAsset.id==aid,StudioAsset.project_id==pid,StudioAsset.tenant_id==auth.tenant.id))
    if not row:raise HTTPException(404,"Asset not found")
    versions=(await db.scalars(select(StudioVersion.schema_json).where(StudioVersion.project_id==pid,StudioVersion.tenant_id==auth.tenant.id))).all()
    if any(row.public_asset_key in x or row.id in x for x in versions):raise HTTPException(409,"Asset is in use")
    path=Path(row.storage_path).resolve()
    if ASSET_ROOT in path.parents and path.exists():path.unlink()
    await db.delete(row);await db.commit();return {"ok":True}
@router.get("/public-assets/{key}")
async def public_asset(key:str,db:AsyncSession=Depends(get_db)):
    row=await db.scalar(select(StudioAsset).where(StudioAsset.public_asset_key==key))
    if not row:raise HTTPException(404,"Asset not found")
    deployment=await db.scalar(select(StudioDeployment).where(StudioDeployment.project_id==row.project_id,StudioDeployment.tenant_id==row.tenant_id,StudioDeployment.status=="active"))
    if not deployment:raise HTTPException(404,"Asset not found")
    path=Path(row.storage_path).resolve()
    if ASSET_ROOT not in path.parents or not path.is_file():raise HTTPException(404,"Asset not found")
    from fastapi.responses import FileResponse
    return FileResponse(path,media_type=row.content_type,headers={"X-Content-Type-Options":"nosniff","Cache-Control":"public, max-age=86400"})

@router.post("/api/studio/projects/{pid}/ai/generate")
@router.post("/api/studio/projects/{pid}/ai/revise")
@router.post("/api/studio/projects/{pid}/ai/theme")
@router.post("/api/studio/projects/{pid}/ai/page")
@router.post("/api/studio/projects/{pid}/ai/section")
@router.post("/api/studio/projects/{pid}/ai/form")
async def ai_edit(pid:str,x:AIInput,request:Request,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:
        p=await service.project(db,auth.tenant.id,pid); current=None
        if request.url.path.endswith(("/revise","/theme","/page","/section","/form")) and p.active_draft_version_id:
            v=await service.version(db,auth.tenant.id,pid,p.active_draft_version_id); current=SiteSchema.model_validate_json(v.schema_json)
        schema=await StudioAI().generate(x.request,current)
        v=await service.save_schema(db,auth.tenant.id,pid,auth.user.id,schema.model_dump(mode="json"),"AI Studio edit")
        return {"version_id":v.id,"version_number":v.version_number,"schema":schema.model_dump(mode="json")}
    except LookupError:raise HTTPException(404,"Project not found")
    except ValueError as e:raise HTTPException(422,str(e)[:1000])

def privacy_hash(value:str): return hashlib.sha256((os.getenv("SESSION_SECRET","")+value).encode()).hexdigest()
_form_rates={}
@router.get("/sites/{public_slug}",response_class=HTMLResponse)
@router.get("/sites/{public_slug}/{page_slug}",response_class=HTMLResponse)
async def public_site(public_slug:str,request:Request,page_slug:str|None=None,db:AsyncSession=Depends(get_db)):
    d=await db.scalar(select(StudioDeployment).where(StudioDeployment.public_slug==public_slug,StudioDeployment.status=="active"))
    if not d:raise HTTPException(404,"Site not found")
    v=await db.scalar(select(StudioVersion).where(StudioVersion.id==d.version_id,StudioVersion.tenant_id==d.tenant_id,StudioVersion.status=="published"))
    if not v:raise HTTPException(404,"Site not found")
    schema=SiteSchema.model_validate_json(v.schema_json); page_slug=page_slug or schema.pages[0].slug
    form_rows=(await db.scalars(select(StudioForm).where(StudioForm.project_id==d.project_id,StudioForm.tenant_id==d.tenant_id,StudioForm.active==True))).all()
    forms={f.form_key:json.loads(f.schema_json) for f in form_rows}
    db.add(StudioPageView(tenant_id=d.tenant_id,project_id=d.project_id,version_id=v.id,public_page_slug=page_slug,ip_hash=privacy_hash(request.client.host if request.client else ""),user_agent_hash=privacy_hash(request.headers.get("user-agent",""))));await db.commit()
    asset_rows=(await db.scalars(select(StudioAsset).where(StudioAsset.project_id==d.project_id,StudioAsset.tenant_id==d.tenant_id))).all()
    asset_map={a.id:f"/public-assets/{a.public_asset_key}" for a in asset_rows}|{a.public_asset_key:f"/public-assets/{a.public_asset_key}" for a in asset_rows}
    try:html=render_site(schema,page_slug,public_slug,forms=forms,assets=asset_map)
    except KeyError:raise HTTPException(404,"Page not found")
    return HTMLResponse(html,headers={"Content-Security-Policy":"default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' https:; form-action 'self'; frame-ancestors 'none'","X-Content-Type-Options":"nosniff","Referrer-Policy":"strict-origin-when-cross-origin","Cache-Control":"public, max-age=60"})

@router.post("/api/public/sites/{public_slug}/forms/{form_key}")
async def public_form(public_slug:str,form_key:str,request:Request,db:AsyncSession=Depends(get_db)):
    if int(request.headers.get("content-length","0") or 0)>50_000:raise HTTPException(413,"Submission too large")
    rate_key=privacy_hash((request.client.host if request.client else "")+public_slug);now=time.monotonic();hits=[x for x in _form_rates.get(rate_key,[]) if now-x<60]
    if len(hits)>=10:raise HTTPException(429,"Too many submissions")
    hits.append(now);_form_rates[rate_key]=hits
    d=await db.scalar(select(StudioDeployment).where(StudioDeployment.public_slug==public_slug,StudioDeployment.status=="active"));
    if not d:raise HTTPException(404,"Form not found")
    form=await db.scalar(select(StudioForm).where(StudioForm.tenant_id==d.tenant_id,StudioForm.project_id==d.project_id,StudioForm.form_key==form_key,StudioForm.active==True))
    if not form:raise HTTPException(404,"Form not found")
    body=(await request.body()).decode("utf-8","replace"); values={k:v[-1][:4000] for k,v in parse_qs(body,keep_blank_values=True,max_num_fields=40).items()}
    if values.pop("website",""):raise HTTPException(400,"Submission rejected")
    page=values.pop("page_slug","home")[:80]; fields=json.loads(form.schema_json); allowed={x["key"] for x in fields}
    if set(values)-allowed:raise HTTPException(422,"Unexpected field")
    for x in fields:
        if x.get("required") and not values.get(x["key"],"").strip():raise HTTPException(422,f'{x["label"]} is required')
    email=values.get("email","").strip().lower() or None; phone=re.sub(r"[^0-9+]","",values.get("phone","")) or None
    row=StudioFormSubmission(tenant_id=d.tenant_id,project_id=d.project_id,form_id=form.id,public_page_slug=page,payload_json=json.dumps(values),normalized_email=email,normalized_phone=phone,source_url=str(request.url)[:500],ip_hash=privacy_hash(request.client.host if request.client else ""));db.add(row);await db.flush()
    contact=None
    if form.destination in {"create_contact","create_contact_and_lead"}:
        if email:contact=await db.scalar(select(Contact).where(Contact.tenant_id==d.tenant_id,func.lower(Contact.email)==email))
        if not contact:contact=Contact(tenant_id=d.tenant_id,name=values.get("name") or values.get("full_name") or "Website visitor",email=email,phone=phone,source=form.source_label);db.add(contact);await db.flush()
        row.created_contact_id=contact.id
    if form.destination in {"create_lead","create_contact_and_lead"}:
        lead=Lead(tenant_id=d.tenant_id,contact_id=contact.id if contact else None,title=form.lead_title_template,stage=form.initial_stage);db.add(lead);await db.flush();row.created_lead_id=lead.id
    row.status="processed";db.add(ActivityEvent(tenant_id=d.tenant_id,event_type="studio_form_received",entity_type="studio_submission",entity_id=row.id,summary="Public website form received",actor="Studio"));await db.commit()
    return HTMLResponse("<!doctype html><meta charset=utf-8><title>Thank you</title><p>Thank you. Your submission was received.</p>",headers={"Content-Security-Policy":"default-src 'none'; style-src 'self'"})

@router.get("/api/studio/projects/{pid}/submissions.csv")
async def export_csv(pid:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    try:await service.project(db,auth.tenant.id,pid)
    except LookupError:raise HTTPException(404,"Project not found")
    rows=(await db.scalars(select(StudioFormSubmission).where(StudioFormSubmission.tenant_id==auth.tenant.id,StudioFormSubmission.project_id==pid))).all(); output=io.StringIO(); writer=csv.writer(output);writer.writerow(["id","created_at","page","status","payload"])
    safe=lambda x:("'"+x) if str(x).startswith(("=","+","-","@")) else x
    for x in rows:writer.writerow([x.id,x.created_at.isoformat(),x.public_page_slug,x.status,safe(x.payload_json)])
    return Response(output.getvalue(),media_type="text/csv",headers={"Content-Disposition":f'attachment; filename="studio-{pid[:8]}-submissions.csv"'})
