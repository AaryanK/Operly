import json
import re
import secrets
from dataclasses import dataclass

from sqlalchemy import desc, select, update

from packages.database.custom_software_models import GeneratedProject, GeneratedProjectChangeSet, ServiceCustomer, ServiceRequest, ServiceStatusEvent


TRANSITIONS = {
    "submitted": {"assigned"},
    "assigned": {"en_route"},
    "en_route": {"completed"},
    "completed": set(),
}

BRANDS = {
    "bicycle": {"name":"Chainline Rescue","vertical":"bicycle","eyebrow":"Bicycle roadside rescue","headline":"Stranded with your bike?","lede":"A trained cycle rescuer comes to you for roadside repair or safe transport.","asset":"Bike or e-bike details","issues":["Flat tire","Broken chain","Crash damage","E-bike issue","Transport needed","Not sure"],"primary":"#ff5c35","accent":"#dfff45","ink":"#101816","surface":"#f3efdf"},
    "auto_glass": {"name":"Clearway Mobile Glass","vertical":"auto_glass","eyebrow":"Mobile auto-glass response","headline":"Broken glass stops here.","lede":"Certified mobile technicians secure or replace your vehicle glass where it sits.","asset":"Vehicle year, make and model","issues":["Windshield chip","Cracked windshield","Side window","Rear glass","Safety cleanup","Not sure"],"primary":"#36d1c4","accent":"#ffe66d","ink":"#071d2b","surface":"#eaf8f7"},
    "pet_transport": {"name":"Kindred Pet Transit","vertical":"pet_transport","eyebrow":"Urgent pet transportation","headline":"Calm transport when it matters.","lede":"Compassionate drivers coordinate safe, direct transport for pets and their people.","asset":"Pet type, size and needs","issues":["Vet transfer","After-hours transport","Mobility support","Long-distance transfer","Return journey","Not sure"],"primary":"#ef6f6c","accent":"#ffd166","ink":"#2b193d","surface":"#fff4e8"},
    "locksmith": {"name":"Afterdark Locksmith","vertical":"locksmith","eyebrow":"24/7 mobile lock response","headline":"Locked out. Not left out.","lede":"Verified local locksmiths restore access without vague arrival windows or surprise dispatch fees.","asset":"Door, lock or vehicle details","issues":["Home lockout","Vehicle lockout","Broken key","Lock replacement","Security concern","Not sure"],"primary":"#7c5cff","accent":"#ffdb57","ink":"#131020","surface":"#f2efff"},
    "mobile_tire": {"name":"Pitstop Anywhere","vertical":"mobile_tire","eyebrow":"On-demand tire service","headline":"The tire shop comes to you.","lede":"Roadside tire crews repair, replace and rebalance at home, at work or where the road stopped you.","asset":"Vehicle and tire size","issues":["Flat tire","Blowout","Tire replacement","Slow leak","Wheel damage","Not sure"],"primary":"#f04b23","accent":"#f5e642","ink":"#151515","surface":"#f1f0e8"},
    "hvac": {"name":"Northline Climate","vertical":"hvac","eyebrow":"Heating and cooling response","headline":"Comfort, back on schedule.","lede":"Licensed technicians diagnose urgent heating and cooling problems with clear arrival and repair updates.","asset":"System type and approximate age","issues":["No cooling","No heat","Water leak","Unusual noise","Air quality","Maintenance"],"primary":"#1976d2","accent":"#ff8a48","ink":"#0c2033","surface":"#edf6fb"},
    "field_it": {"name":"Signal Field Support","vertical":"field_it","eyebrow":"On-site business technology","headline":"Downtime needs a destination.","lede":"Field engineers restore networks, workstations and critical business systems with accountable dispatch.","asset":"Device, system or site details","issues":["Network outage","Workstation failure","Point-of-sale issue","Server alert","New equipment setup","Not sure"],"primary":"#00a884","accent":"#b7ff4a","ink":"#071b18","surface":"#e9f7f3"},
    "commercial_cleaning": {"name":"Reset Crew","vertical":"commercial_cleaning","eyebrow":"Commercial cleaning dispatch","headline":"Ready before the doors open.","lede":"Insured crews handle urgent resets, scheduled deep cleans and post-event recovery with visible progress.","asset":"Site type and approximate size","issues":["Urgent cleanup","Post-event reset","Deep cleaning","Turnover service","Floor care","Custom request"],"primary":"#d93b74","accent":"#6fffe9","ink":"#241025","surface":"#fff0f5"},
}


class DomainError(ValueError): pass
class ConflictError(DomainError): pass


def choose_brand(prompt: str) -> dict:
    text = prompt.lower()
    if "locksmith" in text or "lockout" in text or "locked out" in text:
        return BRANDS["locksmith"]
    if "tire" in text or "tyre" in text:
        return BRANDS["mobile_tire"]
    if "hvac" in text or "heating" in text or "air conditioning" in text:
        return BRANDS["hvac"]
    if "field it" in text or "on-site it" in text or "onsite it" in text or "network support" in text:
        return BRANDS["field_it"]
    if "commercial cleaning" in text or "cleaning crew" in text or "janitorial" in text:
        return BRANDS["commercial_cleaning"]
    if "auto glass" in text or "glass repair" in text or "windshield" in text:
        return BRANDS["auto_glass"]
    if "pet" in text or "animal" in text or "vet" in text:
        return BRANDS["pet_transport"]
    return BRANDS["bicycle"]


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:90]


def artifact_graph() -> dict:
    nodes = [
        {"id":"public.hero","kind":"visual","source":"packages/custom_software/renderer.py","symbol":"render_public","route":"public.home","styles":["brand.primary","brand.accent"]},
        {"id":"public.request-form","kind":"form","source":"apps/api/custom_software_router.py","symbol":"create_request","route":"public.home","entity":"service_request","api":"POST /api/public/service-projects/{slug}/requests","permission":"anonymous:create_service_request","tests":["test_public_request_is_idempotent"]},
        {"id":"dispatch.queue","kind":"visual","source":"packages/custom_software/renderer.py","symbol":"render_dispatch","route":"dispatch.queue","entity":"service_request","api":"GET /api/custom-software/projects/{id}/requests","permission":"staff:dispatch","tests":["test_transition_state_machine"]},
        {"id":"workflow.rescue-lifecycle","kind":"workflow","source":"packages/custom_software/service.py","symbol":"transition_request","entity":"service_request","tests":["test_transition_state_machine"]},
    ]
    return {"schemaVersion":1,"nodes":nodes,"edges":[{"from":"public.request-form","to":"workflow.rescue-lifecycle","type":"starts"},{"from":"dispatch.queue","to":"workflow.rescue-lifecycle","type":"invokes"},{"from":"public.request-form","to":"service_request","type":"writes"}]}


def enrich_design(brand: dict, prompt: str) -> dict:
    result=dict(brand);text=prompt.lower()
    result.update({"displayFont":"condensed","bodyFont":"grotesk","heroMedia":"graphic","requestLayout":"section","spacing":"editorial","imageTreatment":"route-map","architecture":"field_service"})
    if "serif" in text:result["displayFont"]="editorial"
    if "geometric" in text:result["displayFont"]="geometric"
    if "minimal" in text:result["spacing"]="minimal"
    if "dense" in text:result["spacing"]="compact"
    return result


async def create_project(db, tenant_id: str, user_id: str, prompt: str):
    brand = enrich_design(choose_brand(prompt),prompt)
    base = slugify(brand["name"]); slug = base; suffix = 2
    while await db.scalar(select(GeneratedProject.id).where(GeneratedProject.slug==slug)):
        slug=f"{base}-{suffix}";suffix+=1
    row=GeneratedProject(tenant_id=tenant_id,slug=slug,name=brand["name"],vertical=brand["vertical"],prompt=prompt,brand_json=json.dumps(brand),artifact_graph_json=json.dumps(artifact_graph()),created_by=user_id)
    db.add(row);await db.commit();await db.refresh(row);return row


def visual_change(current:dict,request:str,selected:list[str],viewport:str):
    after=dict(current);text=request.lower();impact=[]
    if "bold condensed" in text or "condensed font" in text:after["displayFont"]="condensed-heavy";impact.append("typography.display")
    elif "serif" in text:after["displayFont"]="editorial";impact.append("typography.display")
    if "video hero" in text or "replace this with a video" in text:after["heroMedia"]="video";impact.append("hero.media")
    elif "photo hero" in text:after["heroMedia"]="photo";impact.append("hero.media")
    if "floating panel" in text or "floating form" in text:after["requestLayout"]="floating";impact.append("request.layout")
    if "compact" in text:after["spacing"]="compact";impact.append("spacing")
    if not impact:raise DomainError("The selected visual change is not yet supported")
    allowed={"public.hero","public.request-form"}
    if not set(selected)<=allowed:raise DomainError("This change targets an unsupported artifact")
    return after,{"artifacts":selected,"viewport":viewport,"dependencies":impact,"preserved":["dispatch.queue","workflow.rescue-lifecycle","service_request"]}


async def propose_visual_change(db,project,actor_id,request,selected,viewport):
    before=json.loads(project.brand_json);after,impact=visual_change(before,request,selected,viewport)
    row=GeneratedProjectChangeSet(tenant_id=project.tenant_id,project_id=project.id,base_version=project.version,request=request,selected_artifacts_json=json.dumps(selected),before_json=json.dumps(before),after_json=json.dumps(after),impact_json=json.dumps(impact),created_by=actor_id)
    db.add(row);await db.commit();await db.refresh(row);return row


async def apply_visual_change(db,project,change):
    if change.project_id!=project.id or change.tenant_id!=project.tenant_id:raise LookupError("Change set not found")
    if change.status!="proposed":raise ConflictError("Change set was already applied")
    if change.base_version!=project.version:raise ConflictError("Project changed; create a new proposal")
    project.brand_json=change.after_json;project.version+=1;change.status="applied";await db.commit();await db.refresh(project);return project


async def public_project(db, slug: str):
    row=await db.scalar(select(GeneratedProject).where(GeneratedProject.slug==slug))
    if not row: raise LookupError("Project not found")
    return row


async def create_request(db, project, payload):
    existing=await db.scalar(select(ServiceRequest).where(ServiceRequest.tenant_id==project.tenant_id,ServiceRequest.project_id==project.id,ServiceRequest.idempotency_key==payload.idempotency_key))
    if existing:return existing,False
    customer=ServiceCustomer(tenant_id=project.tenant_id,name=payload.name,phone=payload.phone,email=payload.email);db.add(customer);await db.flush()
    row=ServiceRequest(tenant_id=project.tenant_id,project_id=project.id,customer_id=customer.id,reference=f"SR-{secrets.token_hex(3).upper()}",idempotency_key=payload.idempotency_key,issue_category=payload.issue_category,description=payload.description.strip(),address=payload.address,asset_details=payload.asset_details.strip())
    db.add(row);await db.flush();db.add(ServiceStatusEvent(tenant_id=project.tenant_id,request_id=row.id,to_status="submitted",note="Request received"));await db.commit();await db.refresh(row);return row,True


async def list_requests(db, tenant_id: str, project_id: str):
    return (await db.scalars(select(ServiceRequest).where(ServiceRequest.tenant_id==tenant_id,ServiceRequest.project_id==project_id).order_by(desc(ServiceRequest.created_at)))).all()


async def transition_request(db, tenant_id: str, actor_id: str, request_id: str, target: str, expected_version: int, assigned_to: str|None, note: str):
    row=await db.scalar(select(ServiceRequest).where(ServiceRequest.id==request_id,ServiceRequest.tenant_id==tenant_id))
    if not row:raise LookupError("Request not found")
    if row.version!=expected_version:raise ConflictError("Request changed; refresh and try again")
    if target not in TRANSITIONS.get(row.status,set()):raise DomainError(f"Cannot transition from {row.status} to {target}")
    if target=="assigned" and not (assigned_to or "").strip():raise DomainError("An assignee is required")
    previous=row.status;values={"status":target,"version":expected_version+1}
    if assigned_to:values["assigned_to"]=assigned_to.strip()
    result=await db.execute(update(ServiceRequest).where(ServiceRequest.id==request_id,ServiceRequest.tenant_id==tenant_id,ServiceRequest.version==expected_version,ServiceRequest.status==previous).values(**values).execution_options(synchronize_session=False))
    if result.rowcount!=1:await db.rollback();raise ConflictError("Request changed; refresh and try again")
    db.add(ServiceStatusEvent(tenant_id=tenant_id,request_id=row.id,from_status=previous,to_status=target,actor_id=actor_id,note=note.strip()))
    await db.commit();db.expire(row);await db.refresh(row);return row
