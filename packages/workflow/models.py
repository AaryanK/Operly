from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="disabled", index=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False, index=True
    )


class WorkflowVersion(Base):
    """Immutable editable-definition snapshot.

    ``spec_json`` remains a direct execution field so the engine never needs to infer
    runnable steps from audit metadata. ``snapshot_json`` preserves the corresponding
    human-facing name, description, schedule, and status at the moment this version
    was created.
    """

    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version", name="uq_workflow_versions_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )


class WorkflowSchedule(Base):
    __tablename__ = "workflow_schedules"
    __table_args__ = (
        UniqueConstraint("workflow_id", name="uq_workflow_schedule_workflow"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    schedule_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    schedule_json: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False, default="UTC")
    enabled: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_version_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The exact user whose current Workspace authority is re-resolved for every action.
    # This is the initiator for manual runs and the definition owner for scheduled runs.
    authority_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    initiated_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    trigger_type: Mapped[str] = mapped_column(String(30), nullable=False, default="manual", index=True)
    trigger_payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    dedupe_key: Mapped[str] = mapped_column(String(220), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued", index=True)
    current_step_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    lease_token: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkflowStepRun(Base):
    """Current/summary state for one logical workflow step."""

    __tablename__ = "workflow_step_runs"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "step_key", name="uq_workflow_step_run_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workflow_run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    step_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    capability_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    kernel_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("kernel_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    approval_id: Mapped[str | None] = mapped_column(
        ForeignKey("kernel_approvals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    wait_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkflowStepAttempt(Base):
    """Immutable-in-history execution attempt for an action step.

    The row may transition while one attempt is active (running -> approval wait ->
    completed/failed), but a retry always creates a new row instead of overwriting the
    previous attempt's exact arguments, result, approval, or Kernel correlation.
    """

    __tablename__ = "workflow_step_attempts"
    __table_args__ = (
        UniqueConstraint("step_run_id", "attempt", name="uq_workflow_step_attempt_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workflow_run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_step_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running", index=True)
    request_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    kernel_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("kernel_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    approval_id: Mapped[str | None] = mapped_column(
        ForeignKey("kernel_approvals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkflowTraceEvent(Base):
    __tablename__ = "workflow_trace_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    step_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_step_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    step_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_step_attempts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False, default="system")
    actor_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    capability_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    kernel_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("kernel_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    approval_id: Mapped[str | None] = mapped_column(
        ForeignKey("kernel_approvals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
