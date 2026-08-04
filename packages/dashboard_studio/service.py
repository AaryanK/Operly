import json
import logging
import re
from sqlalchemy import delete,func,select,update
from sqlalchemy.ext.asyncio import AsyncSession
from packages.dashboard_studio.registry import ROLES,VARIANTS,WIDTHS,get_component,screen_manifest
from packages.dashboard_studio.schemas import ChangeSetInput,OperationInput
from packages.database.dashboard_studio_models import DashboardCustomization,DashboardChangeOperation,DashboardChangeSet,AppConfigurationVersion,DashboardStudioAudit

ALLOWED={"title","label","shown","order","width","variant","visibility","action_binding"}
LOG = logging.getLogger("operly.dashboard_studio")
class DashboardStudioError(ValueError):pass
class DashboardStudioService:
    @staticmethod
    def require_editor(role):
        if role not in {"owner","manager"}:raise PermissionError("Owner or manager role required")
    @staticmethod
    def validate_changes(component,changes):
        allowed=set(component.editable_properties)&ALLOWED
        unknown=set(changes)-allowed
        if unknown:raise DashboardStudioError(f"Unsupported properties: {', '.join(sorted(unknown))}")
        clean={}
        for key,value in changes.items():
            if key in {"title","label"}:
                if not isinstance(value,str) or not value.strip() or len(value)>200:raise DashboardStudioError(f"Invalid {key}")
                clean[key]=value.strip()
            elif key=="shown":
                if not isinstance(value,bool):raise DashboardStudioError("shown must be boolean")
                clean[key]=value
            elif key=="order":
                if not isinstance(value,int) or not 1<=value<=100:raise DashboardStudioError("order must be between 1 and 100")
                clean[key]=value
            elif key=="width":
                if value not in WIDTHS:raise DashboardStudioError("Unsupported width")
                clean[key]=value
            elif key=="variant":
                if value not in VARIANTS:raise DashboardStudioError("Unsupported variant")
                clean[key]=value
            elif key=="visibility":
                if not isinstance(value,list) or not value or not set(value)<=ROLES:raise DashboardStudioError("Unsupported visibility roles")
                clean[key]=list(dict.fromkeys(value))
            elif key=="action_binding":
                if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}",value):raise DashboardStudioError("Invalid action binding")
                clean[key]=value
        return clean
    @staticmethod
    async def overrides(db,tenant_id):
        rows=(await db.scalars(select(DashboardCustomization).where(DashboardCustomization.tenant_id==tenant_id))).all()
        result={}
        for row in rows:
            component=get_component(row.component_id)
            if not component:
                LOG.warning("dashboard_config_invalid workspace_id=%s component_id=%s reason=unknown_component",tenant_id,row.component_id)
                continue
            try:
                value=json.loads(row.override_json)
                if not isinstance(value,dict):raise ValueError("override is not an object")
                result[row.component_id]=DashboardStudioService.validate_changes(component,value)
            except (json.JSONDecodeError,DashboardStudioError,ValueError,TypeError) as error:
                LOG.warning("dashboard_config_invalid workspace_id=%s component_id=%s reason=%s",tenant_id,row.component_id,type(error).__name__)
        return result
    @classmethod
    async def effective_screen(cls,db,tenant_id,screen_id,role):
        overrides=await cls.overrides(db,tenant_id);result=[]
        for entry in screen_manifest(screen_id):
            values=dict(entry["editable_properties"]);values.update(overrides.get(entry["id"],{}));entry["effective_properties"]=values;entry.pop("editable_properties",None)
            entry["visible_for_role"]=role in values.get("visibility",[]) and values.get("shown",True)
            result.append(entry)
        active=await db.scalar(select(AppConfigurationVersion).where(AppConfigurationVersion.tenant_id==tenant_id,AppConfigurationVersion.active==True).order_by(AppConfigurationVersion.version_number.desc()))
        return {"screen_id":screen_id,"components":result,"active_version":active.id if active else None,"version_number":active.version_number if active else 0}
    @classmethod
    async def create_change_set(cls,db,tenant_id,user_id,role,payload:ChangeSetInput):
        cls.require_editor(role);overrides=await cls.overrides(db,tenant_id);before={};after={};validated=[]
        active=await db.scalar(select(AppConfigurationVersion).where(AppConfigurationVersion.tenant_id==tenant_id,AppConfigurationVersion.active==True).order_by(AppConfigurationVersion.version_number.desc()))
        for op in payload.operations:
            component=get_component(op.component_id)
            if not component or component.page_id not in {payload.screen_id,"global"}:raise DashboardStudioError("Unknown component for this screen")
            if op.operation not in {"update_component","move_component","change_visibility"}:raise DashboardStudioError("Unsupported operation")
            clean=cls.validate_changes(component,op.changes);current=dict(component.editable_properties);current.update(overrides.get(component.id,{}));updated={**current,**clean};before[component.id]=current;after[component.id]=updated;validated.append((op,clean))
        row=DashboardChangeSet(tenant_id=tenant_id,screen_id=payload.screen_id,originating_chat_message=payload.originating_chat_message,target_component_ids_json=json.dumps(list(after)),before_json=json.dumps(before),after_json=json.dumps(after),explanation=payload.explanation,validation_json=json.dumps({"valid":True,"errors":[],"base_version_id":active.id if active else None}),status="proposed",created_by=user_id);db.add(row);await db.flush()
        for position,(op,clean) in enumerate(validated):db.add(DashboardChangeOperation(tenant_id=tenant_id,change_set_id=row.id,position=position,operation=op.operation,component_id=op.component_id,changes_json=json.dumps(clean)))
        db.add(DashboardStudioAudit(tenant_id=tenant_id,actor_id=user_id,action="change_set_proposed",entity_id=row.id,details_json=json.dumps({"screen_id":payload.screen_id,"components":list(after)})));await db.commit();LOG.info("change_set_proposed workspace_id=%s change_set_id=%s screen_id=%s",tenant_id,row.id,payload.screen_id);return row
    @staticmethod
    async def change_set(db,tenant_id,change_set_id):
        row=await db.scalar(select(DashboardChangeSet).where(DashboardChangeSet.id==change_set_id,DashboardChangeSet.tenant_id==tenant_id))
        if not row:raise LookupError("Change set not found")
        return row
    @classmethod
    async def preview(cls,db,tenant_id,change_set_id,role):
        cls.require_editor(role);row=await cls.change_set(db,tenant_id,change_set_id)
        if row.status not in {"proposed","previewing"}:raise DashboardStudioError("Change set cannot be previewed")
        try:overlay=json.loads(row.after_json)
        except json.JSONDecodeError as error:raise DashboardStudioError("Stored preview configuration is invalid") from error
        row.status="previewing";await db.commit();LOG.info("change_set_previewed workspace_id=%s change_set_id=%s screen_id=%s",tenant_id,row.id,row.screen_id);return overlay
    @classmethod
    async def apply(cls,db,tenant_id,change_set_id,user_id,role):
        cls.require_editor(role);row=await cls.change_set(db,tenant_id,change_set_id)
        if row.status not in {"proposed","previewing","approved"}:raise DashboardStudioError("Change set cannot be applied")
        validation=json.loads(row.validation_json)
        active=await db.scalar(select(AppConfigurationVersion).where(AppConfigurationVersion.tenant_id==tenant_id,AppConfigurationVersion.active==True).order_by(AppConfigurationVersion.version_number.desc()))
        if validation.get("base_version_id") != (active.id if active else None):raise DashboardStudioError("Change set is stale; create a new proposal from the active version")
        try:after=json.loads(row.after_json)
        except json.JSONDecodeError as error:raise DashboardStudioError("Stored change configuration is invalid") from error
        if not isinstance(after,dict):raise DashboardStudioError("Stored change configuration is invalid")
        validated_after={}
        for component_id,values in after.items():
            component=get_component(component_id)
            if not component:raise DashboardStudioError("Registered component no longer exists")
            # Revalidate the delta at apply time; never trust stored/model JSON alone.
            validated_after[component_id]=cls.validate_changes(component,{k:v for k,v in values.items() if component.editable_properties.get(k)!=v})
        claimed=await db.execute(update(DashboardChangeSet).where(DashboardChangeSet.id==row.id,DashboardChangeSet.tenant_id==tenant_id,DashboardChangeSet.status.in_(["proposed","previewing","approved"])).values(status="applying"))
        if claimed.rowcount!=1:raise DashboardStudioError("Change set cannot be applied")
        for component_id,clean in validated_after.items():
            component=get_component(component_id)
            existing=await db.scalar(select(DashboardCustomization).where(DashboardCustomization.tenant_id==tenant_id,DashboardCustomization.component_id==component_id))
            if not existing:existing=DashboardCustomization(tenant_id=tenant_id,screen_id=component.page_id,component_id=component_id,override_json="{}",updated_by=user_id);db.add(existing)
            existing.override_json=json.dumps(clean);existing.updated_by=user_id
        for v in (await db.scalars(select(AppConfigurationVersion).where(AppConfigurationVersion.tenant_id==tenant_id,AppConfigurationVersion.active==True))).all():v.active=False
        maximum=await db.scalar(select(func.max(AppConfigurationVersion.version_number)).where(AppConfigurationVersion.tenant_id==tenant_id)) or 0
        if maximum==0:
            db.add(AppConfigurationVersion(tenant_id=tenant_id,version_number=1,snapshot_json="{}",summary="Source dashboard defaults",affected_json="[]",created_by=user_id,active=False));maximum=1
        number=maximum+1
        snapshot=await cls.overrides(db,tenant_id)
        # Include pending ORM values which may not yet be visible through a SELECT.
        snapshot.update(validated_after)
        version=AppConfigurationVersion(tenant_id=tenant_id,version_number=number,snapshot_json=json.dumps(snapshot),summary=row.explanation[:500],affected_json=row.target_component_ids_json,originating_change_set_id=row.id,created_by=user_id,active=True);db.add(version);await db.flush();row.status="applied";row.applied_version_id=version.id
        db.add(DashboardStudioAudit(tenant_id=tenant_id,actor_id=user_id,action="change_set_applied",entity_id=row.id,details_json=json.dumps({"version_id":version.id})));await db.commit();LOG.info("change_set_applied workspace_id=%s change_set_id=%s version_id=%s",tenant_id,row.id,version.id);return version
    @classmethod
    async def reject(cls,db,tenant_id,change_set_id,user_id,role):
        cls.require_editor(role);row=await cls.change_set(db,tenant_id,change_set_id)
        if row.status not in {"proposed","previewing"}:raise DashboardStudioError("Change set cannot be rejected")
        row.status="rejected";db.add(DashboardStudioAudit(tenant_id=tenant_id,actor_id=user_id,action="change_set_rejected",entity_id=row.id));await db.commit();LOG.info("change_set_rejected workspace_id=%s change_set_id=%s",tenant_id,row.id);return row
    @classmethod
    async def rollback(cls,db,tenant_id,version_id,user_id,role):
        cls.require_editor(role);source=await db.scalar(select(AppConfigurationVersion).where(AppConfigurationVersion.id==version_id,AppConfigurationVersion.tenant_id==tenant_id))
        if not source:raise LookupError("Version not found")
        try:snapshot=json.loads(source.snapshot_json)
        except json.JSONDecodeError as error:raise DashboardStudioError("Stored version configuration is invalid") from error
        if not isinstance(snapshot,dict):raise DashboardStudioError("Stored version configuration is invalid")
        for cid,values in snapshot.items():
            component=get_component(cid)
            if not component:raise DashboardStudioError("Historical component no longer exists")
            cls.validate_changes(component,values)
        await db.execute(delete(DashboardCustomization).where(DashboardCustomization.tenant_id==tenant_id))
        for cid,values in snapshot.items():db.add(DashboardCustomization(tenant_id=tenant_id,screen_id=get_component(cid).page_id,component_id=cid,override_json=json.dumps(values),updated_by=user_id))
        active_versions=(await db.scalars(select(AppConfigurationVersion).where(AppConfigurationVersion.tenant_id==tenant_id,AppConfigurationVersion.active==True))).all()
        for v in active_versions:
            v.active=False
            if v.originating_change_set_id:
                active_change=await db.scalar(select(DashboardChangeSet).where(DashboardChangeSet.id==v.originating_change_set_id,DashboardChangeSet.tenant_id==tenant_id))
                if active_change and active_change.status=="applied":active_change.status="rolled_back"
        number=(await db.scalar(select(func.max(AppConfigurationVersion.version_number)).where(AppConfigurationVersion.tenant_id==tenant_id)) or 0)+1
        version=AppConfigurationVersion(tenant_id=tenant_id,version_number=number,snapshot_json=json.dumps(snapshot),summary=f"Rollback to version {source.version_number}",affected_json=source.affected_json,source_version_id=source.id,created_by=user_id,active=True);db.add(version);await db.flush();db.add(DashboardStudioAudit(tenant_id=tenant_id,actor_id=user_id,action="version_rolled_back",entity_id=source.id,details_json=json.dumps({"new_version_id":version.id,"new_version_number":number})));await db.commit();LOG.info("version_rolled_back workspace_id=%s source_version_id=%s version_id=%s",tenant_id,source.id,version.id);return version

def operations_from_request(message,selected):
    text=message.strip();lower=text.lower();ops=[]
    rename=re.search(r"(?:rename (?:this|it)(?: to)?|call (?:this|it))\s+[\"“]?(.+?)[\"”]?[.!]?$",text,re.I)
    for component in selected:
        changes={};op="update_component"
        if rename:changes["label" if component.type=="NavigationItem" else "title"]=rename.group(1).strip(' "“” .')[:200]
        if "same width" in lower:changes["width"]="medium"
        if "hide" in lower and "employee" in lower:changes["visibility"]=["owner","manager"];op="change_visibility"
        elif lower.startswith("hide") or " hide this" in lower:changes["shown"]=False
        elif lower.startswith("show") or " show this" in lower:changes["shown"]=True
        if changes:ops.append(OperationInput(operation=op,component_id=component.id,changes=changes))
    if not ops:raise DashboardStudioError("That request is not a supported safe customization yet")
    return ops
