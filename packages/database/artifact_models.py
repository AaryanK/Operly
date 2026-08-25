from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class ArtifactRecord(Base):
    """Durable scoped file/object owned by one Operly execution scope.

    Bytes live in Postgres for the first durable implementation so every web/worker
    replica and the trusted sandbox runner can address the same artifact IDs. The
    storage_kind/storage_key fields deliberately keep the contract ready for a later
    object-store backend without changing agent-visible references.
    """

    __tablename__ = "operly_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(String(220), nullable=False, index=True)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    parent_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(80), default="agent")
    version: Mapped[int] = mapped_column(Integer, default=1)
    storage_kind: Mapped[str] = mapped_column(String(32), default="database")
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    __table_args__ = (
        CheckConstraint(
            "(scope_kind = 'workspace' AND tenant_id IS NOT NULL AND owner_user_id IS NULL) OR "
            "(scope_kind = 'personal' AND tenant_id IS NULL AND owner_user_id IS NOT NULL)",
            name="ck_operly_artifact_scope_owner",
        ),
        Index("ix_operly_artifact_scope_created", "scope_kind", "scope_id", "created_at"),
        Index("ix_operly_artifact_scope_sha", "scope_kind", "scope_id", "sha256"),
    )


class AgentRunRecord(Base):
    """Durable checkpoint for the shared Operly/Studio/workflow agent runtime."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(String(220), nullable=False, index=True)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    surface: Mapped[str] = mapped_column(String(60), default="unknown")
    channel: Mapped[str] = mapped_column(String(60), default="operly")
    conversation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    workflow_job_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    objective: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(32), default="running", index=True)
    plan_json: Mapped[str] = mapped_column(Text, default="{}")
    checkpoint_json: Mapped[str] = mapped_column(Text, default="{}")
    artifact_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    pending_approval_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(scope_kind = 'workspace' AND tenant_id IS NOT NULL AND owner_user_id IS NULL) OR "
            "(scope_kind = 'personal' AND tenant_id IS NULL AND owner_user_id IS NOT NULL)",
            name="ck_agent_runs_scope_owner",
        ),
        Index("ix_agent_run_scope_updated", "scope_kind", "scope_id", "updated_at"),
    )


class AgentRunEventRecord(Base):
    """Append-only operational checkpoints for resume/debug/audit."""

    __tablename__ = "agent_run_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_run_event_sequence"),
        Index("ix_agent_run_event_run_created", "run_id", "created_at"),
    )
