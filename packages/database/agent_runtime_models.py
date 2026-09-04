from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class AgentRuntimeRun(Base):
    __tablename__ = "agent_runtime_runs"
    __table_args__ = (
        CheckConstraint(
            "(scope_kind = 'workspace' AND workspace_id IS NOT NULL) OR "
            "(scope_kind = 'personal' AND workspace_id IS NULL AND owner_user_id IS NOT NULL)",
            name="ck_agent_runtime_run_scope_owner",
        ),
    )

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    authority_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    principal_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)
    budget_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued", index=True)
    current_step_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    lease_token: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentRuntimeStep(Base):
    __tablename__ = "agent_runtime_steps"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "step_id", name="uq_agent_runtime_step_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runtime_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    capability_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    request_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kernel_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("kernel_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    approval_id: Mapped[str | None] = mapped_column(
        ForeignKey("kernel_approvals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentRuntimeStepAttempt(Base):
    __tablename__ = "agent_runtime_step_attempts"
    __table_args__ = (
        UniqueConstraint("agent_step_id", "attempt", name="uq_agent_runtime_step_attempt"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runtime_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_step_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runtime_steps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    capability_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
