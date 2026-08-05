import json,re
from packages.database.custom_software_models import SandboxGenerationJob,SandboxJobEvent
STATES=("planned","queued","submitted","generating","installing","building","testing","previewing","completed","failed","cancelled","expired")
TRANSITIONS={"planned":{"queued","cancelled"},"queued":{"submitted","failed","cancelled","expired"},"submitted":{"generating","failed","cancelled","expired"},"generating":{"installing","failed","cancelled","expired"},"installing":{"building","failed","cancelled","expired"},"building":{"testing","failed","cancelled","expired"},"testing":{"previewing","failed","cancelled","expired"},"previewing":{"completed","failed","cancelled","expired"}}
def redact(value):return re.sub(r"(?i)(bearer\s+|token[=:]\s*)[^\s,]+",r"\1[REDACTED]",str(value))[:4000]
async def create_job(db,tenant_id,user_id,plan_row):
 if plan_row.status!="approved" or not plan_row.approved_version:raise ValueError("Sandbox jobs require an approved plan")
 row=SandboxGenerationJob(tenant_id=tenant_id,plan_id=plan_row.id,approved_plan_version=plan_row.approved_version,created_by=user_id,resource_json=json.dumps({"cpu":2,"memoryMb":2048,"timeoutSeconds":900}));db.add(row);await db.flush();db.add(SandboxJobEvent(tenant_id=tenant_id,job_id=row.id,state="planned"));await db.commit();await db.refresh(row);return row
async def transition_job(db,row,state,details=None):
 if state not in TRANSITIONS.get(row.state,set()):raise ValueError(f"Invalid sandbox transition {row.state} -> {state}")
 row.state=state
 if state=="failed":row.failure_message=redact((details or {}).get("message","Runner failed"))
 if state=="completed":
  result=details or {};required={"previewUrl","sourceArchive","testReport","artifactGraph","buildDigest"}
  if not required<=set(result):raise ValueError("Completed runner result is incomplete")
  row.result_json=json.dumps(result)
 db.add(SandboxJobEvent(tenant_id=row.tenant_id,job_id=row.id,state=state,details_json=json.dumps({k:redact(v) for k,v in (details or {}).items()})));await db.commit();await db.refresh(row);return row
