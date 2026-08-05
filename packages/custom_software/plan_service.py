import json
from sqlalchemy import desc, select

from packages.custom_software.planner import build_software_plan, revise_plan
from packages.custom_software.schema import SoftwarePlan
from packages.database.custom_software_models import SoftwarePlanRecord, SoftwarePlanVersion

class PlanConflict(ValueError):pass

async def create_plan(db,tenant_id,user_id,prompt):
    planned=build_software_plan(prompt);row=SoftwarePlanRecord(tenant_id=tenant_id,prompt=prompt,created_by=user_id);db.add(row);await db.flush()
    version=SoftwarePlanVersion(tenant_id=tenant_id,plan_id=row.id,version=1,plan_json=planned.model_dump_json(),created_by=user_id);db.add(version);await db.commit();await db.refresh(row);return row,version,planned

async def owned_plan(db,tenant_id,plan_id):
    row=await db.get(SoftwarePlanRecord,plan_id)
    if not row or row.tenant_id!=tenant_id:raise LookupError("Software plan not found")
    return row

async def plan_version(db,row,version=None):
    number=version or row.current_version
    result=await db.scalar(select(SoftwarePlanVersion).where(SoftwarePlanVersion.plan_id==row.id,SoftwarePlanVersion.tenant_id==row.tenant_id,SoftwarePlanVersion.version==number))
    if not result:raise LookupError("Software plan version not found")
    return result,SoftwarePlan.model_validate_json(result.plan_json)

async def revise(db,row,user_id,request,expected):
    if row.current_version!=expected:raise PlanConflict("Software plan changed; refresh before revising")
    _,current=await plan_version(db,row);updated=revise_plan(current,request);row.current_version+=1;row.status="draft";row.approved_version=None
    version=SoftwarePlanVersion(tenant_id=row.tenant_id,plan_id=row.id,version=row.current_version,plan_json=updated.model_dump_json(),revision_request=request,created_by=user_id);db.add(version);await db.commit();await db.refresh(row);return version,updated

async def approve(db,row,expected):
    if row.current_version!=expected:raise PlanConflict("Software plan changed; refresh before approving")
    if row.approved_version==expected:return row
    row.approved_version=expected;row.status="approved";await db.commit();await db.refresh(row);return row

def plan_json(row,version,plan):
    return {"id":row.id,"status":row.status,"currentVersion":row.current_version,"approvedVersion":row.approved_version,"version":version.version,"prompt":row.prompt,"plan":plan.model_dump()}
