from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class ProfileSubject(Base):
    """Explicit subject for company/product intelligence inside one workspace."""

    __tablename__ = "profile_subjects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    reference_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    inherits_workspace: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "reference_id", name="uq_profile_subject_identity"),
        Index("ix_profile_subject_tenant_kind", "tenant_id", "kind"),
    )


class ScopedCompanyProfile(Base):
    __tablename__ = "scoped_company_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("profile_subjects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    profile_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    field_meta_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    unresolved_conflicts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ScopedCompanyEvidence(Base):
    __tablename__ = "scoped_company_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("profile_subjects.id", ondelete="CASCADE"), nullable=False, index=True)
    field_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    action_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    research_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    owner_initiated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    owner_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("subject_id", "field_key", "content_hash", "source_type", name="uq_scoped_company_evidence_fact"),
        Index("ix_scoped_evidence_subject_field", "subject_id", "field_key", "stale", "superseded"),
    )


class SolutionContextSnapshot(Base):
    """Immutable approved owner/Solution context used by one generation trajectory."""

    __tablename__ = "solution_context_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    solution_id: Mapped[str] = mapped_column(ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    owner_objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    context_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    context_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class StudioModelAttempt(Base):
    """Durable provider/model attempt beneath a Studio model turn."""

    __tablename__ = "studio_model_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("studio_agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    model_turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_attempt_index: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model_resource_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    error_classification: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failover_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "model_turn_index", "provider_attempt_index", name="uq_studio_model_attempt_order"),
    )


class ConversationArtifact(Base):
    """First-class attachment/derived artifact retained across conversation turns."""

    __tablename__ = "conversation_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    principal_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id", ondelete="SET NULL"), nullable=True, index=True)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    artifact_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    content_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    __table_args__ = (
        Index("ix_conversation_artifact_scope", "channel", "conversation_id", "created_at"),
    )


class PersonalWorkspaceDelegation(Base):
    """Revocable personal-to-workspace grant; never implies connector ownership transfer."""

    __tablename__ = "personal_workspace_delegations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    connector_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    scope_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    grant_type: Mapped[str] = mapped_column(String(30), nullable=False, default="persistent")
    action_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_personal_delegation_lookup", "tenant_id", "user_id", "capability_id", "status"),
    )


class DelegatedCapabilityAudit(Base):
    __tablename__ = "delegated_capability_audit"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    delegation_id: Mapped[str] = mapped_column(ForeignKey("personal_workspace_delegations.id", ondelete="CASCADE"), nullable=False, index=True)
    principal_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False)
    action_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    used_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)
