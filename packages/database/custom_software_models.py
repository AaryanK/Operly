from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class GeneratedProject(Base):
    __tablename__ = "generated_projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    slug: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    vertical: Mapped[str] = mapped_column(String(50))
    prompt: Mapped[str] = mapped_column(Text)
    brand_json: Mapped[str] = mapped_column(Text)
    artifact_graph_json: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_users.id"))
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("software_plans.id"), nullable=True, index=True)
    approved_plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    architecture_pack: Mapped[str] = mapped_column(String(50), default="field_service", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("slug", name="uq_generated_project_slug"),)


class SoftwarePlanRecord(Base):
    __tablename__ = "software_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    prompt: Mapped[str] = mapped_column(Text)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    approved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SoftwarePlanVersion(Base):
    __tablename__ = "software_plan_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("software_plans.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    plan_json: Mapped[str] = mapped_column(Text)
    requirement_ledger_json: Mapped[str] = mapped_column(Text, default="[]")
    plan_tree_json: Mapped[str] = mapped_column(Text, default="[]")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    semantic_diff_json: Mapped[str] = mapped_column(Text, default="{}")
    revision_request: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("plan_id", "version", name="uq_software_plan_version"),)

class PlanningModelInvocation(Base):
    __tablename__ = "planning_model_invocations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("software_plans.id", ondelete="CASCADE"), index=True)
    plan_version: Mapped[int] = mapped_column(Integer, index=True)
    node_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(40), index=True)
    planning_mode: Mapped[str] = mapped_column(String(30), index=True)
    provider: Mapped[str] = mapped_column(String(80)); model_id: Mapped[str] = mapped_column(String(160))
    request_id: Mapped[str] = mapped_column(String(160), index=True); context_digest: Mapped[str] = mapped_column(String(64), index=True)
    prompt_version: Mapped[str] = mapped_column(String(40), default="v1"); attempt: Mapped[int] = mapped_column(Integer, default=1)
    structured_output_json: Mapped[str] = mapped_column(Text, default="{}"); raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_errors_json: Mapped[str] = mapped_column(Text, default="[]"); retry_history_json: Mapped[str] = mapped_column(Text, default="[]")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0); input_tokens: Mapped[int] = mapped_column(Integer, default=0); output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    failure_classification: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    parent_invocation_id: Mapped[str | None] = mapped_column(ForeignKey("planning_model_invocations.id"), nullable=True)
    retry_of_id: Mapped[str | None] = mapped_column(ForeignKey("planning_model_invocations.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class PlanningWorkItem(Base):
    __tablename__ = "planning_work_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True); plan_id: Mapped[str] = mapped_column(ForeignKey("software_plans.id", ondelete="CASCADE"), index=True)
    plan_version: Mapped[int] = mapped_column(Integer); node_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    work_type: Mapped[str] = mapped_column(String(40), index=True); priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    state: Mapped[str] = mapped_column(String(30), default="queued", index=True); attempts: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[str] = mapped_column(Text, default="{}"); findings_json: Mapped[str] = mapped_column(Text, default="[]"); blocked_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True); created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow); updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("plan_id", "plan_version", "node_id", "work_type", name="uq_planning_work_item"),)

class SandboxGenerationJob(Base):
    __tablename__="sandbox_generation_jobs"
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True);plan_id:Mapped[str]=mapped_column(ForeignKey("software_plans.id"));approved_plan_version:Mapped[int]=mapped_column(Integer);state:Mapped[str]=mapped_column(String(30),default="planned",index=True);runner_job_id:Mapped[str|None]=mapped_column(String(120),nullable=True);attempts:Mapped[int]=mapped_column(Integer,default=0);resource_json:Mapped[str]=mapped_column(Text,default="{}");result_json:Mapped[str]=mapped_column(Text,default="{}");failure_message:Mapped[str|None]=mapped_column(Text,nullable=True);created_by:Mapped[str]=mapped_column(ForeignKey("app_users.id"));created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow);updated_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow,onupdate=datetime.utcnow)
class SandboxJobEvent(Base):
    __tablename__="sandbox_job_events"
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True);job_id:Mapped[str]=mapped_column(ForeignKey("sandbox_generation_jobs.id",ondelete="CASCADE"),index=True);state:Mapped[str]=mapped_column(String(30));details_json:Mapped[str]=mapped_column(Text,default="{}");created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)

class GeneratedSourceBundle(Base):
    __tablename__="generated_source_bundles"
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True);plan_id:Mapped[str]=mapped_column(ForeignKey("software_plans.id"),index=True);plan_version:Mapped[int]=mapped_column(Integer);source_version:Mapped[int]=mapped_column(Integer);application_id:Mapped[str]=mapped_column(String(120),index=True);bundle_digest:Mapped[str]=mapped_column(String(80),index=True);manifest_json:Mapped[str]=mapped_column(Text);files_json:Mapped[str]=mapped_column(Text);provenance_json:Mapped[str]=mapped_column(Text);created_by:Mapped[str]=mapped_column(ForeignKey("app_users.id"));created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    __table_args__=(UniqueConstraint("tenant_id","application_id","source_version",name="uq_generated_source_version"),)

class RunnerBuildRecord(Base):
    __tablename__="runner_build_records"
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True);plan_id:Mapped[str]=mapped_column(ForeignKey("software_plans.id"),index=True);source_bundle_id:Mapped[str]=mapped_column(ForeignKey("generated_source_bundles.id"),index=True);runner_job_id:Mapped[str|None]=mapped_column(String(160),nullable=True,index=True);idempotency_key:Mapped[str]=mapped_column(String(120));state:Mapped[str]=mapped_column(String(40),default="created",index=True);runner_implementation:Mapped[str]=mapped_column(String(80));isolation_profile:Mapped[str]=mapped_column(String(80));submission_json:Mapped[str]=mapped_column(Text);result_json:Mapped[str]=mapped_column(Text,default="{}");failure_classification:Mapped[str|None]=mapped_column(String(60),nullable=True);attempt:Mapped[int]=mapped_column(Integer,default=1);parent_build_id:Mapped[str|None]=mapped_column(ForeignKey("runner_build_records.id"),nullable=True);created_by:Mapped[str]=mapped_column(ForeignKey("app_users.id"));created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow);started_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True);completed_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True);updated_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow,onupdate=datetime.utcnow)
    __table_args__=(UniqueConstraint("tenant_id","idempotency_key",name="uq_runner_build_idempotency"),)

class RunnerBuildEvent(Base):
    __tablename__="runner_build_events"
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True);build_id:Mapped[str]=mapped_column(ForeignKey("runner_build_records.id",ondelete="CASCADE"),index=True);sequence:Mapped[int]=mapped_column(Integer);state:Mapped[str]=mapped_column(String(40),index=True);event_type:Mapped[str]=mapped_column(String(60));message:Mapped[str]=mapped_column(Text);details_json:Mapped[str]=mapped_column(Text,default="{}");created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    __table_args__=(UniqueConstraint("build_id","sequence",name="uq_runner_build_event_sequence"),)

class RunnerArtifactRecord(Base):
    __tablename__="runner_artifacts"
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True);build_id:Mapped[str]=mapped_column(ForeignKey("runner_build_records.id",ondelete="CASCADE"),index=True);kind:Mapped[str]=mapped_column(String(60));name:Mapped[str]=mapped_column(String(200));digest:Mapped[str]=mapped_column(String(80));size_bytes:Mapped[int]=mapped_column(Integer);reference:Mapped[str]=mapped_column(Text);metadata_json:Mapped[str]=mapped_column(Text,default="{}");created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)

class RunnerPreviewRecord(Base):
    __tablename__="runner_previews"
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True);build_id:Mapped[str]=mapped_column(ForeignKey("runner_build_records.id"),index=True);runner_preview_id:Mapped[str]=mapped_column(String(160));state:Mapped[str]=mapped_column(String(30),default="active",index=True);target_url:Mapped[str]=mapped_column(Text);expires_at:Mapped[datetime]=mapped_column(DateTime,index=True);stopped_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True);created_by:Mapped[str]=mapped_column(ForeignKey("app_users.id"));created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)

class RunnerRepairRecord(Base):
    __tablename__="runner_repairs"
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid);tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True);build_id:Mapped[str]=mapped_column(ForeignKey("runner_build_records.id"),index=True);source_bundle_id:Mapped[str]=mapped_column(ForeignKey("generated_source_bundles.id"));attempt:Mapped[int]=mapped_column(Integer);classification:Mapped[str]=mapped_column(String(60));repair_prompt:Mapped[str]=mapped_column(Text);patch_json:Mapped[str]=mapped_column(Text);status:Mapped[str]=mapped_column(String(30),default="proposed");created_by:Mapped[str]=mapped_column(ForeignKey("app_users.id"));created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)


class GeneratedProjectChangeSet(Base):
    __tablename__ = "generated_project_change_sets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("generated_projects.id", ondelete="CASCADE"), index=True)
    base_version: Mapped[int] = mapped_column(Integer)
    request: Mapped[str] = mapped_column(Text)
    selected_artifacts_json: Mapped[str] = mapped_column(Text, default="[]")
    before_json: Mapped[str] = mapped_column(Text)
    after_json: Mapped[str] = mapped_column(Text)
    impact_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("app_users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ServiceCustomer(Base):
    __tablename__ = "service_customers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ServiceRequest(Base):
    __tablename__ = "service_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("generated_projects.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("service_customers.id"), index=True)
    reference: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    issue_category: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    address: Mapped[str] = mapped_column(String(500))
    asset_details: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(30), default="submitted", index=True)
    assigned_to: Mapped[str | None] = mapped_column(String(160), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("tenant_id", "project_id", "idempotency_key", name="uq_service_request_idempotency"),)


class ServiceStatusEvent(Base):
    __tablename__ = "service_status_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("service_requests.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
