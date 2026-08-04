from datetime import datetime
from uuid import uuid4
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from packages.database.db import Base

def uid(): return str(uuid4())

class StudioProject(Base):
    __tablename__="studio_projects"
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    name: Mapped[str]=mapped_column(String(200)); slug: Mapped[str]=mapped_column(String(100))
    description: Mapped[str]=mapped_column(Text,default=""); status: Mapped[str]=mapped_column(String(30),default="draft")
    active_draft_version_id: Mapped[str|None]=mapped_column(String(36),nullable=True)
    published_version_id: Mapped[str|None]=mapped_column(String(36),nullable=True)
    created_by: Mapped[str]=mapped_column(String(36)); created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    updated_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow,onupdate=datetime.utcnow)
    __table_args__=(UniqueConstraint("tenant_id","slug",name="uq_studio_project_slug"),)

class StudioVersion(Base):
    __tablename__="studio_versions"
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid); tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    project_id: Mapped[str]=mapped_column(ForeignKey("studio_projects.id"),index=True); version_number: Mapped[int]=mapped_column(Integer)
    schema_json: Mapped[str]=mapped_column(Text); change_summary: Mapped[str]=mapped_column(String(500),default="")
    created_by: Mapped[str]=mapped_column(String(36)); status: Mapped[str]=mapped_column(String(30),default="draft")
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow); published_at: Mapped[datetime|None]=mapped_column(DateTime,nullable=True)
    __table_args__=(UniqueConstraint("project_id","version_number",name="uq_studio_version_number"),)

class StudioDeployment(Base):
    __tablename__="studio_deployments"
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid); tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    project_id: Mapped[str]=mapped_column(ForeignKey("studio_projects.id"),index=True); version_id: Mapped[str]=mapped_column(ForeignKey("studio_versions.id"))
    public_slug: Mapped[str]=mapped_column(String(120),unique=True,index=True); status: Mapped[str]=mapped_column(String(30),default="active")
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow); updated_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow,onupdate=datetime.utcnow)

class StudioForm(Base):
    __tablename__="studio_forms"
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid); tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    project_id: Mapped[str]=mapped_column(ForeignKey("studio_projects.id"),index=True); name: Mapped[str]=mapped_column(String(200)); form_key: Mapped[str]=mapped_column(String(80))
    destination: Mapped[str]=mapped_column(String(40),default="store_only"); lead_title_template: Mapped[str]=mapped_column(String(300),default="Website inquiry")
    initial_stage: Mapped[str]=mapped_column(String(50),default="new"); default_value: Mapped[int]=mapped_column(Integer,default=0); source_label: Mapped[str]=mapped_column(String(100),default="Studio website")
    active: Mapped[bool]=mapped_column(Boolean,default=True); schema_json: Mapped[str]=mapped_column(Text,default="{}")
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow); updated_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow,onupdate=datetime.utcnow)
    __table_args__=(UniqueConstraint("project_id","form_key",name="uq_studio_form_key"),)

class StudioFormSubmission(Base):
    __tablename__="studio_form_submissions"
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid); tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True)
    project_id: Mapped[str]=mapped_column(ForeignKey("studio_projects.id"),index=True); form_id: Mapped[str]=mapped_column(ForeignKey("studio_forms.id"),index=True)
    public_page_slug: Mapped[str]=mapped_column(String(100)); payload_json: Mapped[str]=mapped_column(Text); normalized_email: Mapped[str|None]=mapped_column(String(320),nullable=True)
    normalized_phone: Mapped[str|None]=mapped_column(String(80),nullable=True); source_url: Mapped[str]=mapped_column(String(500),default=""); ip_hash: Mapped[str]=mapped_column(String(64))
    status: Mapped[str]=mapped_column(String(30),default="received"); created_contact_id: Mapped[str|None]=mapped_column(String(36),nullable=True); created_lead_id: Mapped[str|None]=mapped_column(String(36),nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)

class StudioAsset(Base):
    __tablename__="studio_assets"
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid); tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True); project_id: Mapped[str]=mapped_column(ForeignKey("studio_projects.id"),index=True)
    public_asset_key: Mapped[str]=mapped_column(String(80),unique=True,index=True); original_filename: Mapped[str]=mapped_column(String(255)); stored_filename: Mapped[str]=mapped_column(String(100)); content_type: Mapped[str]=mapped_column(String(50)); size_bytes: Mapped[int]=mapped_column(Integer); storage_path: Mapped[str]=mapped_column(Text); width: Mapped[int|None]=mapped_column(Integer,nullable=True); height: Mapped[int|None]=mapped_column(Integer,nullable=True); created_by: Mapped[str]=mapped_column(String(36)); created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)

class StudioAuditEvent(Base):
    __tablename__="studio_audit_events"
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid); tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True); project_id: Mapped[str]=mapped_column(ForeignKey("studio_projects.id"),index=True); version_id: Mapped[str|None]=mapped_column(String(36),nullable=True); actor_id: Mapped[str]=mapped_column(String(120)); actor_channel: Mapped[str]=mapped_column(String(40),default="web"); action: Mapped[str]=mapped_column(String(80)); details_json: Mapped[str]=mapped_column(Text,default="{}"); created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)

class StudioPageView(Base):
    __tablename__="studio_page_views"
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid); tenant_id: Mapped[str]=mapped_column(ForeignKey("tenants.id"),index=True); project_id: Mapped[str]=mapped_column(ForeignKey("studio_projects.id"),index=True); version_id: Mapped[str]=mapped_column(ForeignKey("studio_versions.id")); public_page_slug: Mapped[str]=mapped_column(String(100)); ip_hash: Mapped[str]=mapped_column(String(64)); user_agent_hash: Mapped[str]=mapped_column(String(64)); created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
