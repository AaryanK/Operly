import json, re
from copy import deepcopy
from datetime import datetime, timedelta
from sqlalchemy import desc, select, update

from packages.application_builder.catalog import MODULES, PALETTE
from packages.application_builder.schema import ApplicationManifest, ProposalRequest, RecordInput, blank_manifest
from packages.database.application_builder_models import ApplicationAuditEvent, ApplicationChangeSet, ApplicationPreviewSession, ApplicationVersion, ManagedApplication, ManagedRecord


class BuilderError(ValueError): pass
class UnsupportedRequestError(BuilderError): pass


def slugify(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:90] or "application"


def _component(cid, kind, label, parent=None, order=0, **properties):
    return {"id":cid,"type":kind,"label":label,"parentId":parent,"order":order,"properties":properties}


def _install(manifest, module_id):
    if module_id not in MODULES: raise BuilderError("Unknown capability module")
    for dependency in MODULES[module_id].get("dependencies", []): _install(manifest, dependency)
    if not any(x["moduleId"] == module_id for x in manifest["modules"]):
        manifest["modules"].append({"moduleId":module_id,"version":MODULES[module_id]["version"],"configuration":{}})


def plan_request(request: ProposalRequest, current: ApplicationManifest):
    text=request.message.lower().strip(); scope=request.context.selectionScope; selected=request.context.selectedIds
    if any(word in text for word in ["this","here","these"]) and scope=="application" and not selected:
        raise BuilderError("Please select the page, region, or component you mean.")
    after=deepcopy(current.model_dump(mode="json")); operations=[]
    def op(name,target,details,risk="medium"):
        operations.append({"operation":name,"target":target,"after":details,"dependencies":[],"risk":risk,"validation":{"valid":True}})
    if "secure login" in text or "login system" in text:
        for mid in ["authentication","dashboard","navigation"]: _install(after,mid); op("install_module",mid,{"moduleId":mid,"version":1},"high" if mid=="authentication" else "medium")
        if not any(p["id"]=="login" for p in after["pages"]):
            ids=["login-page","login-card","login-form","login-email","login-password","login-submit"]
            after["pages"].append({"id":"login","name":"Login","route":"/login","protected":False,"componentIds":[ids[0]]})
            after["components"] += [_component(ids[0],"Page","Login page"),_component(ids[1],"Card","Login",ids[0]),_component(ids[2],"Form","Secure login",ids[1]),_component(ids[3],"EmailInput","Email",ids[2],0,label_text="Email"),_component(ids[4],"PasswordInput","Password",ids[2],1,label_text="Password"),_component(ids[5],"SubmitButton","Sign in",ids[2],2,text="Sign in")]
            after["routes"] += [{"route":"/login","protected":False},{"route":"/","protected":True}]
            after["permissions"] += [{"role":"manager","actions":["run_application"]},{"role":"employee","actions":["run_application"]}]
            op("create_page","login",{"security":{"identity":"shared_operly","passwordHashing":"PBKDF2-HMAC-SHA256","sessions":"signed HttpOnly SameSite cookies","sessionFixation":"rotated on login","rateLimiting":"bounded platform login limiter","enumerationSafe":True,"redirectValidation":True},"auditEvents":["login","logout","failed_login","permission_denied"],"tests":["login","logout","invalid login","protected route","role enforcement"]},"high")
    elif "customer-management" in text or "customer management" in text:
        nested=ProposalRequest(message="Add a secure login page",context=request.context);nested_plan=plan_request(nested,ApplicationManifest.model_validate(after));after=nested_plan["after"];operations.extend(nested_plan["operations"])
        for mid in ["crud_entity","form","data_table"]:_install(after,mid);op("install_module",mid,{"moduleId":mid,"version":1})
        after["entities"].append({"id":"customer","name":"Customer","fields":[{"id":"name","name":"Name","type":"text","required":True},{"id":"email","name":"Email","type":"email","required":False}]})
        after["pages"].append({"id":"customers","name":"Customers","route":"/customers","protected":True,"componentIds":["customers-page"]})
        after["components"] += [_component("customers-page","Page","Customers"),_component("customer-form","Form","Customer form","customers-page",0,entityId="customer"),_component("customer-table","DataTable","Customers","customers-page",1,entityId="customer")]
        op("create_entity","customer",after["entities"][-1]);op("create_page","customers",after["pages"][-1])
    elif "entire application" in text or "whole application" in text or ("dark green" in text and "cream" in text):
        if scope not in {"application"}: raise BuilderError("Select the application or clear the selection for a global theme change.")
        if "dark green" in text:after["theme"]["primary"]="forest"
        elif "green" in text:after["theme"]["primary"]="emerald"
        if "cream" in text:after["theme"]["background"]=after["theme"]["surface"]="cream"
        _install(after,"theme");op("update_theme","application",after["theme"])
    elif "orange" in text and scope in {"component","multi"} and selected:
        changed=[]
        for component in after["components"]:
            if component["id"] in selected: component["overrides"]["primary"]="orange";changed.append(component["id"])
        if not changed: raise BuilderError("The selected component is not in this application.")
        op("update_component",changed,{"tokenOverride":{"primary":"orange"}})
    elif "follow-up task" in text and selected:
        _install(after,"workflow");binding={"id":f"follow-up-{selected[0]}","componentId":selected[0],"event":"on_click","action":"create_record","configuration":{"entityId":"task","preset":{"status":"open"}}};after["workflows"].append(binding);op("create_workflow",binding["id"],binding)
    else: raise UnsupportedRequestError("The request requires model-driven application synthesis.")
    validated=ApplicationManifest.model_validate(after)
    return {"operations":operations,"after":validated.model_dump(mode="json"),"risk":"high" if any(x["risk"]=="high" for x in operations) else "medium"}


class ApplicationBuilderService:
    @staticmethod
    async def application(db,tenant_id,application_id):
        row=await db.scalar(select(ManagedApplication).where(ManagedApplication.id==application_id,ManagedApplication.tenant_id==tenant_id))
        if not row: raise LookupError("Application not found")
        return row
    @classmethod
    async def current(cls,db,tenant_id,application_id):
        app=await cls.application(db,tenant_id,application_id);version=await db.get(ApplicationVersion,app.active_version_id)
        if not version or version.tenant_id!=tenant_id: raise BuilderError("Application has no active version")
        return app,version,ApplicationManifest.model_validate_json(version.manifest_json)
    @classmethod
    async def create(cls,db,tenant_id,user_id,name,description=""):
        if not name.strip():raise BuilderError("Application name is required")
        base=slugify(name);slug=base;suffix=2
        while await db.scalar(select(ManagedApplication.id).where(ManagedApplication.tenant_id==tenant_id,ManagedApplication.slug==slug)):slug=f"{base}-{suffix}";suffix+=1
        app=ManagedApplication(tenant_id=tenant_id,slug=slug,name=name.strip()[:200],description=description[:2000],created_by=user_id);db.add(app);await db.flush()
        manifest=blank_manifest(app.id,app.name);version=ApplicationVersion(tenant_id=tenant_id,application_id=app.id,version_number=1,manifest_json=manifest.model_dump_json(),summary="Blank application",created_by=user_id,active=True);db.add(version);await db.flush();app.active_version_id=version.id
        db.add(ApplicationAuditEvent(tenant_id=tenant_id,application_id=app.id,actor_id=user_id,action="application_created",details_json="{}"));await db.commit();return app,version
    @classmethod
    async def propose(cls,db,tenant_id,user_id,role,payload):
        if role!="owner":raise PermissionError("Only owners can change managed applications")
        app,version,manifest=await cls.current(db,tenant_id,payload.context.applicationId)
        if payload.context.workspaceId!=tenant_id or payload.context.activeVersionId!=version.id:raise BuilderError("Studio context is stale or belongs to another workspace")
        try:
            plan=plan_request(payload,manifest)
        except UnsupportedRequestError:
            from packages.application_builder.ai import ApplicationBuilderAI
            try:plan=await ApplicationBuilderAI().plan(payload,manifest)
            except ValueError as exc:raise BuilderError(str(exc)) from exc
        row=ApplicationChangeSet(tenant_id=tenant_id,application_id=app.id,base_version_id=version.id,request=payload.message,scope=payload.context.selectionScope,operations_json=json.dumps(plan["operations"]),before_json=manifest.model_dump_json(),after_json=json.dumps(plan["after"]),validation_json=json.dumps({"valid":True,"errors":[]}),risk=plan["risk"],created_by=user_id);db.add(row);await db.flush();db.add(ApplicationAuditEvent(tenant_id=tenant_id,application_id=app.id,actor_id=user_id,action="change_set_proposed",details_json=json.dumps({"changeSetId":row.id,"scope":row.scope,"planner":"model" if any(x["operation"]=="synthesize_application" for x in plan["operations"]) else "deterministic"})));await db.commit();return row
    @classmethod
    async def change_set(cls,db,tenant_id,change_id):
        row=await db.scalar(select(ApplicationChangeSet).where(ApplicationChangeSet.id==change_id,ApplicationChangeSet.tenant_id==tenant_id))
        if not row:raise LookupError("Change set not found")
        return row
    @classmethod
    async def preview(cls,db,tenant_id,user_id,change_id):
        row=await cls.change_set(db,tenant_id,change_id)
        if row.status not in {"proposed","previewing"}:raise BuilderError("Change set cannot be previewed")
        ApplicationManifest.model_validate_json(row.after_json);row.status="previewing";preview=ApplicationPreviewSession(tenant_id=tenant_id,application_id=row.application_id,change_set_id=row.id,expires_at=datetime.utcnow()+timedelta(hours=1),created_by=user_id);db.add(preview);await db.commit();return preview
    @classmethod
    async def apply(cls,db,tenant_id,user_id,role,change_id):
        if role!="owner":raise PermissionError("Only owners can apply changes")
        row=await cls.change_set(db,tenant_id,change_id);app,current,_=await cls.current(db,tenant_id,row.application_id)
        if row.base_version_id!=current.id:raise BuilderError("Base version is stale")
        if row.status not in {"proposed","previewing"}:raise BuilderError("Change set cannot be applied")
        manifest=ApplicationManifest.model_validate_json(row.after_json);number=(await db.scalar(select(ApplicationVersion.version_number).where(ApplicationVersion.application_id==app.id).order_by(desc(ApplicationVersion.version_number)).limit(1)) or 0)+1
        await db.execute(update(ApplicationVersion).where(ApplicationVersion.application_id==app.id,ApplicationVersion.active.is_(True)).values(active=False))
        version=ApplicationVersion(tenant_id=tenant_id,application_id=app.id,version_number=number,manifest_json=manifest.model_dump_json(),summary=row.request[:500],source_version_id=current.id,created_by=user_id,active=True);db.add(version);await db.flush();app.active_version_id=version.id;row.status="applied";row.applied_version_id=version.id;db.add(ApplicationAuditEvent(tenant_id=tenant_id,application_id=app.id,actor_id=user_id,action="change_set_applied",details_json=json.dumps({"changeSetId":row.id,"version":number})));await db.commit();return version
    @classmethod
    async def rollback(cls,db,tenant_id,user_id,role,application_id,version_id):
        if role!="owner":raise PermissionError("Only owners can roll back")
        app,current,_=await cls.current(db,tenant_id,application_id);source=await db.scalar(select(ApplicationVersion).where(ApplicationVersion.id==version_id,ApplicationVersion.application_id==app.id,ApplicationVersion.tenant_id==tenant_id))
        if not source:raise LookupError("Version not found")
        ApplicationManifest.model_validate_json(source.manifest_json);number=(await db.scalar(select(ApplicationVersion.version_number).where(ApplicationVersion.application_id==app.id).order_by(desc(ApplicationVersion.version_number)).limit(1)))+1;await db.execute(update(ApplicationVersion).where(ApplicationVersion.application_id==app.id,ApplicationVersion.active.is_(True)).values(active=False));version=ApplicationVersion(tenant_id=tenant_id,application_id=app.id,version_number=number,manifest_json=source.manifest_json,summary=f"Rollback to version {source.version_number}",source_version_id=source.id,created_by=user_id,active=True);db.add(version);await db.flush();app.active_version_id=version.id;db.add(ApplicationAuditEvent(tenant_id=tenant_id,application_id=app.id,actor_id=user_id,action="application_rolled_back",details_json=json.dumps({"sourceVersionId":source.id})));await db.commit();return version
