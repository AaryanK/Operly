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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("slug", name="uq_generated_project_slug"),)


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
