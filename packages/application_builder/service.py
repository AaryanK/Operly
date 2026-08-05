import json, logging, os, re
from decimal import Decimal, InvalidOperation
from datetime import date
from copy import deepcopy
from datetime import datetime, timedelta
from sqlalchemy import desc, select, update

from packages.application_builder.catalog import MODULES, PALETTE
from packages.application_builder.schema import ApplicationManifest, ProposalRequest, RecordInput, blank_manifest
from packages.database.application_builder_models import ApplicationAuditEvent, ApplicationChangeSet, ApplicationPreviewSession, ApplicationVersion, ManagedApplication, ManagedRecord


class BuilderError(ValueError): pass
class UnsupportedRequestError(BuilderError): pass
class RecordValidationError(BuilderError):
    def __init__(self,errors):
        self.errors=errors;super().__init__("Managed record validation failed")
class BuilderGenerationError(BuilderError):
    def __init__(self,details):self.details=details;super().__init__("The AI could not produce a valid application plan after an automatic repair attempt.")

logger = logging.getLogger("operly.application_builder")
DEPLOYED_COMMIT_SHA = (os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_COMMIT_SHA") or os.getenv("SOURCE_VERSION") or "unknown")[:64]

LOGIN_INTENT_PATTERNS = (
    re.compile(r"\b(?:add|create|build|make|implement|enable|set\s*up)\b.{0,40}\b(?:secure\s+)?(?:user\s+)?login\b", re.I),
    re.compile(r"\b(?:add|create|build|make|implement|enable|set\s*up)\b.{0,40}\bauth(?:entication)?\b", re.I),
    re.compile(r"\blogin\s+(?:page|screen|form|system|flow)\b", re.I),
)
CUSTOMER_NOTEBOOK_PATTERNS = (
    re.compile(r"\b(?:customer|buyer)s?\b.*\b(?:purchases?|bought|purchased|notebook|tracker)\b", re.I),
    re.compile(r"\b(?:purchases?|bought|purchased)\b.*\b(?:customer|buyer|reach|contact)\b", re.I),
    re.compile(r"\bremember\b.*\bwho\s+bought\s+what\b", re.I),
)


def detect_intent(text: str) -> str | None:
    """Classify allowlisted builder requests without sending content to a model."""
    normalized = " ".join(text.strip().split())
    if any(pattern.search(normalized) for pattern in CUSTOMER_NOTEBOOK_PATTERNS):
        return "customer_notebook"
    if "customer management" in normalized.lower() or "customer-management" in normalized.lower():
        return None
    if any(pattern.search(normalized) for pattern in LOGIN_INTENT_PATTERNS):
        return "secure_login"
    return None


def unresolved_selection_reference(text: str, scope: str, selected: list[str]) -> bool:
    if scope != "application" or selected:
        return False
    normalized = " ".join(text.lower().split())
    target_noun = r"(?:button|table|form|field|page|section|component|region|card)"
    return bool(re.search(rf"\b(?:this|that|selected|current)\s+{target_noun}\b", normalized) or re.search(r"\b(?:move|remove|delete|change|hide|show)\b.*\b(?:this|that|here|there|selected|current)\b", normalized))


def safe_log_text(text: str) -> str:
    """Retain request wording while removing credential-like assignments."""
    pattern = r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|cookie)\b\s*[:=]\s*\S+"
    return re.sub(pattern, r"\1=[REDACTED]", text)[:4000]


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
    if unresolved_selection_reference(text,scope,selected):
        raise BuilderError("Please select the page, region, or component you mean.")
    after=deepcopy(current.model_dump(mode="json")); operations=[]
    def op(name,target,details,risk="medium"):
        operations.append({"operation":name,"target":target,"after":details,"dependencies":[],"risk":risk,"validation":{"valid":True}})
    intent=detect_intent(text)
    if intent == "secure_login":
        for mid in ["authentication","dashboard","navigation"]: _install(after,mid); op("install_module",mid,{"moduleId":mid,"version":1},"high" if mid=="authentication" else "medium")
        if not any(p["id"]=="login" for p in after["pages"]):
            ids=["login-page","login-card","login-form","login-email","login-password","login-submit"]
            after["pages"].append({"id":"login","name":"Login","route":"/login","protected":False,"componentIds":[ids[0]]})
            after["components"] += [_component(ids[0],"Page","Login page"),_component(ids[1],"Card","Login",ids[0]),_component(ids[2],"Form","Secure login",ids[1],authFlow=True),_component(ids[3],"EmailInput","Email",ids[2],0,label_text="Email"),_component(ids[4],"PasswordInput","Password",ids[2],1,label_text="Password"),_component(ids[5],"SubmitButton","Sign in",ids[2],2,text="Sign in")]
            after["routes"] += [{"route":"/login","protected":False},{"route":"/","protected":True}]
            after["permissions"] += [{"role":"manager","actions":["run_application"]},{"role":"employee","actions":["run_application"]}]
            op("create_page","login",{"security":{"identity":"shared_operly","passwordHashing":"PBKDF2-HMAC-SHA256","sessions":"signed HttpOnly SameSite cookies","sessionFixation":"rotated on login","rateLimiting":"bounded platform login limiter","enumerationSafe":True,"redirectValidation":True},"auditEvents":["login","logout","failed_login","permission_denied"],"tests":["login","logout","invalid login","protected route","role enforcement"]},"high")
    elif intent == "customer_notebook":
        for mid in ["crud_entity","form","data_table","navigation","permissions"]:_install(after,mid);op("install_module",mid,{"moduleId":mid,"version":1})
        after["entities"] += [
            {"id":"customer","name":"Customer","fields":[{"id":"name","name":"Name","type":"text","required":True,"maxLength":120},{"id":"phone","name":"Phone","type":"phone","required":False,"maxLength":40},{"id":"email","name":"Email","type":"email","required":False,"maxLength":320},{"id":"notes","name":"Notes","type":"long_text","required":False,"maxLength":2000},{"id":"created_at","name":"Created","type":"datetime","required":False}]},
            {"id":"purchase","name":"Purchase","fields":[{"id":"customer_name","name":"Customer","type":"text","required":True,"maxLength":120},{"id":"item","name":"Item or description","type":"text","required":True,"maxLength":300},{"id":"amount","name":"Amount","type":"decimal","required":False,"minimum":0},{"id":"purchase_date","name":"Purchase date","type":"date","required":False},{"id":"notes","name":"Notes","type":"long_text","required":False,"maxLength":2000}]},
        ]
        pages=[("home","Overview","/"),("customers","Customers","/customers"),("add-customer","Add customer","/customers/new"),("purchases","Purchases","/purchases"),("add-purchase","Add purchase","/purchases/new")]
        after["pages"] += [{"id":pid,"name":name,"route":route,"protected":True,"componentIds":[f"{pid}-page"]} for pid,name,route in pages]
        after["routes"] += [{"route":route,"protected":True} for _,_,route in pages]
        after["components"] += [
            _component("home-page","Page","Customer notebook"),_component("home-section","Section","Overview","home-page"),_component("home-heading","Heading","Customer notebook","home-section",text="Customer notebook"),_component("home-copy","TextBlock","Track customers and purchases safely.","home-section",1,text="Track customers and purchases safely."),
            _component("customers-page","Page","Customers"),_component("customer-table","DataTable","Customers","customers-page",entityId="customer",columns=["name","phone","email","notes"]),
            _component("add-customer-page","Page","Add customer"),_component("customer-form","Form","Add customer","add-customer-page",entityId="customer",mode="create",successMessage="Customer saved."),
            _component("customer-name","TextInput","Name","customer-form",fieldId="name"),_component("customer-phone","TextInput","Phone","customer-form",1,fieldId="phone"),_component("customer-email","EmailInput","Email","customer-form",2,fieldId="email"),_component("customer-notes","TextInput","Notes","customer-form",3,fieldId="notes"),_component("customer-submit","SubmitButton","Save customer","customer-form",4,text="Save customer"),
            _component("purchases-page","Page","Purchases"),_component("purchase-table","DataTable","Purchases","purchases-page",entityId="purchase",columns=["customer_name","item","amount","purchase_date","notes"]),
            _component("add-purchase-page","Page","Add purchase"),_component("purchase-form","Form","Add purchase","add-purchase-page",entityId="purchase",mode="create",successMessage="Purchase saved."),
            _component("purchase-customer","TextInput","Customer","purchase-form",fieldId="customer_name"),_component("purchase-item","TextInput","Item or description","purchase-form",1,fieldId="item"),_component("purchase-amount","TextInput","Amount","purchase-form",2,fieldId="amount"),_component("purchase-date","DateInput","Purchase date","purchase-form",3,fieldId="purchase_date"),_component("purchase-notes","TextInput","Notes","purchase-form",4,fieldId="notes"),_component("purchase-submit","SubmitButton","Save purchase","purchase-form",5,text="Save purchase"),
        ]
        after["permissions"] += [{"role":"manager","actions":["run_application","create_record","list_records"]},{"role":"employee","actions":["run_application","create_record","list_records"]}]
        op("create_customer_notebook","application",{"entities":["customer","purchase"],"pages":[x[0] for x in pages]},"medium")
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
        intent=detect_intent(payload.message);planner="deterministic" if intent else "ollama"
        logger.info("builder_request %s",json.dumps({"request_text":safe_log_text(payload.message),"workspace_id":tenant_id,"application_id":app.id,"planner":planner,"intent":intent,"deployed_git_commit_sha":DEPLOYED_COMMIT_SHA},ensure_ascii=False))
        try:
            plan=plan_request(payload,manifest)
        except UnsupportedRequestError:
            from packages.application_builder.ai import ApplicationBuilderAI, ManifestGenerationError
            try:plan=await ApplicationBuilderAI().plan(payload,manifest)
            except ManifestGenerationError as exc:
                safe={"planner":"ollama","intent":intent,"details":exc.details}
                initial=[item for item in exc.details.get("errors",[]) if item.get("stage")=="initial"]
                repair=[item for item in exc.details.get("errors",[]) if item.get("stage")=="repair"]
                db.add(ApplicationAuditEvent(tenant_id=tenant_id,application_id=app.id,actor_id=user_id,action="model_manifest_generation_failed",details_json=json.dumps({**safe,"details":{**exc.details,"errors":initial}})))
                db.add(ApplicationAuditEvent(tenant_id=tenant_id,application_id=app.id,actor_id=user_id,action="model_manifest_repair_failed",details_json=json.dumps({**safe,"details":{**exc.details,"errors":repair}})))
                await db.commit();raise BuilderGenerationError(exc.details) from exc
            except ValueError as exc:
                logger.warning("builder_validation_failure %s",json.dumps({"workspace_id":tenant_id,"application_id":app.id,"planner":"ollama","intent":intent,"schema_validation_errors":str(exc),"repair_attempt_result":"failed"}))
                raise BuilderError(str(exc)) from exc
        logger.info("builder_plan_validated %s",json.dumps({"workspace_id":tenant_id,"application_id":app.id,"planner":planner,"intent":intent,"schema_validation_errors":[],"repair_attempt_result":"not_attempted" if planner=="deterministic" else "valid"}))
        if intent=="customer_notebook":db.add(ApplicationAuditEvent(tenant_id=tenant_id,application_id=app.id,actor_id=user_id,action="customer_notebook_proposed",details_json=json.dumps({"planner":"deterministic","versionId":version.id})))
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

    @staticmethod
    def _allowed(manifest,role,action):
        if role=="owner":return True
        return any(item.get("role")==role and action in item.get("actions",[]) for item in manifest.permissions)

    @staticmethod
    def _normalize_record(entity,data):
        declared={field.id:field for field in entity.fields};errors=[];normalized={}
        for key in sorted(set(data)-set(declared)):errors.append({"field":key,"category":"undeclared_field","message":"This field is not declared by the active application."})
        for field in entity.fields:
            value=data.get(field.id)
            if value is None or value=="":
                if field.id=="created_at" and field.type=="datetime":normalized[field.id]=datetime.utcnow().isoformat();continue
                if field.required:errors.append({"field":field.id,"category":"required","message":f"{field.name} is required."})
                continue
            try:
                if field.type in {"text","long_text","email","phone","status","relation","user_reference"}:
                    value=str(value).strip()
                    limit=field.maxLength or (10000 if field.type=="long_text" else 500)
                    if len(value)>limit:raise ValueError(f"Must be at most {limit} characters.")
                    if field.type=="email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",value):raise ValueError("Enter a valid email address.")
                    if field.type=="phone" and not re.fullmatch(r"[0-9+().\-\s]{3,40}",value):raise ValueError("Enter a valid phone number.")
                    if field.type=="status" and value not in field.options:raise ValueError("Choose an allowed value.")
                elif field.type=="integer":value=int(value)
                elif field.type=="decimal":value=Decimal(str(value));value=float(value)
                elif field.type=="boolean":
                    if isinstance(value,bool):pass
                    elif str(value).lower() in {"true","1","yes","on"}:value=True
                    elif str(value).lower() in {"false","0","no","off"}:value=False
                    else:raise ValueError("Enter a valid boolean value.")
                elif field.type=="date":value=date.fromisoformat(str(value)).isoformat()
                elif field.type=="datetime":value=datetime.fromisoformat(str(value).replace("Z","+00:00")).isoformat()
                if field.type in {"integer","decimal"}:
                    if field.minimum is not None and value<field.minimum:raise ValueError(f"Must be at least {field.minimum}.")
                    if field.maximum is not None and value>field.maximum:raise ValueError(f"Must be at most {field.maximum}.")
                normalized[field.id]=value
            except (ValueError,TypeError,InvalidOperation) as exc:errors.append({"field":field.id,"category":"invalid_type","message":str(exc) or f"Invalid {field.type} value."})
        if errors:raise RecordValidationError(errors)
        return normalized

    @classmethod
    async def create_record(cls,db,tenant_id,user_id,role,application_id,entity_id,payload):
        app,version,manifest=await cls.current(db,tenant_id,application_id)
        if payload.versionId!=version.id:raise BuilderError("Application version is stale; refresh before submitting.")
        if not cls._allowed(manifest,role,"create_record"):raise PermissionError("This role cannot create managed records")
        entity=next((x for x in manifest.entities if x.id==entity_id),None)
        if not entity:raise LookupError("Entity not found")
        form=next((x for x in manifest.components if x.id==payload.formId and x.type=="Form"),None)
        if not form or form.properties.get("entityId")!=entity_id or form.properties.get("mode","create")!="create":raise BuilderError("Form is not allowed to create this entity")
        existing=await db.scalar(select(ManagedRecord).where(ManagedRecord.tenant_id==tenant_id,ManagedRecord.application_id==app.id,ManagedRecord.idempotency_key==payload.idempotencyKey))
        if existing:return existing,json.loads(existing.data_json),False
        try:normalized=cls._normalize_record(entity,payload.data)
        except RecordValidationError as exc:
            db.add(ApplicationAuditEvent(tenant_id=tenant_id,application_id=app.id,actor_id=user_id,action="managed_record_validation_failed",details_json=json.dumps({"entityId":entity_id,"formId":payload.formId,"categories":sorted({x["category"] for x in exc.errors}),"fields":[x["field"] for x in exc.errors]})));await db.commit();raise
        row=ManagedRecord(tenant_id=tenant_id,application_id=app.id,entity_id=entity_id,application_version_id=version.id,idempotency_key=payload.idempotencyKey,data_json=json.dumps(normalized),created_by=user_id);db.add(row);await db.flush();db.add(ApplicationAuditEvent(tenant_id=tenant_id,application_id=app.id,actor_id=user_id,action="managed_record_created",details_json=json.dumps({"recordId":row.id,"entityId":entity_id,"versionId":version.id,"formId":payload.formId})));await db.commit();return row,normalized,True

    @classmethod
    async def list_records(cls,db,tenant_id,role,application_id,entity_id,limit=50):
        app,version,manifest=await cls.current(db,tenant_id,application_id)
        if not cls._allowed(manifest,role,"list_records"):raise PermissionError("This role cannot list managed records")
        entity=next((x for x in manifest.entities if x.id==entity_id),None)
        if not entity:raise LookupError("Entity not found")
        rows=(await db.scalars(select(ManagedRecord).where(ManagedRecord.tenant_id==tenant_id,ManagedRecord.application_id==app.id,ManagedRecord.entity_id==entity_id).order_by(desc(ManagedRecord.created_at)).limit(min(max(limit,1),100)))).all();declared={x.id for x in entity.fields}
        return rows,[{key:value for key,value in json.loads(row.data_json).items() if key in declared} for row in rows],version
