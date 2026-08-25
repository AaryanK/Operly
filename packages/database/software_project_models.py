from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class SoftwareProjectRecord(Base):
    """Canonical project identity layered over current runtime implementations."""

    __tablename__ = "software_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="draft", index=True)
    active_source_version_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    active_runtime_id: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # Compatibility identity. New projects may begin without a legacy runtime;
    # migrated projects keep an immutable reference to the existing implementation.
    legacy_runtime_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    legacy_runtime_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)

    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "legacy_runtime_type",
            "legacy_runtime_reference",
            name="uq_software_project_legacy_runtime",
        ),
    )


class SoftwareSourceVersionRecord(Base):
    """Immutable authoritative source tree for one canonical SoftwareProject.

    Legacy Studio/generated source rows may be imported into this table, but new
    AgentRuntime/Studio writes use this identity directly.  The source tree is
    backend state; sandboxes only receive temporary materializations of it.
    """

    __tablename__ = "software_source_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("software_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_source_id: Mapped[str | None] = mapped_column(
        ForeignKey("software_source_versions.id"), nullable=True, index=True
    )
    runtime_profile: Mapped[str] = mapped_column(String(160), nullable=False, default="unknown")
    bundle_digest: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    files_json: Mapped[str] = mapped_column(Text, nullable=False)
    provenance_json: Mapped[str] = mapped_column(Text, default="{}")
    change_summary: Mapped[str] = mapped_column(Text, default="")
    originating_run_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    legacy_source_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    legacy_source_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "source_version", name="uq_software_source_project_version"),
        UniqueConstraint(
            "tenant_id",
            "legacy_source_type",
            "legacy_source_reference",
            name="uq_software_source_legacy_reference",
        ),
    )


class ServiceBindingRecord(Base):
    """Project-scoped handle to an Operly capability; never stores provider secrets."""

    __tablename__ = "service_bindings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("software_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    semantic_name: Mapped[str] = mapped_column(String(160), nullable=False)
    capability_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    capability_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0.0")
    binding_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="capability_gateway")
    principal_scope: Mapped[str] = mapped_column(String(80), nullable=False, default="project_runtime")
    configuration_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active", index=True)
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "semantic_name",
            name="uq_service_binding_project_semantic_name",
        ),
    )
