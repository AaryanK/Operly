from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class AgentComputerSessionRecord(Base):
    """Durable, workspace-scoped task session for the governed Agent Computer UI.

    The session never stores provider credentials or shell handles. It stores only the
    selected Workspace capability invocation and the Kernel run/approval handles needed
    to resume that exact invocation.
    """

    __tablename__ = "agent_computer_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_users.id"), nullable=False, index=True)
    principal_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    objective: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="ready", index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("software_projects.id"), nullable=True, index=True
    )
    solution_id: Mapped[str | None] = mapped_column(
        ForeignKey("solutions.id"), nullable=True, index=True
    )
    arguments_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    current_capability_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    current_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    current_run_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    approval_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_agent_computer_session_scope_updated", "tenant_id", "user_id", "updated_at"),
    )


class AgentComputerStepRecord(Base):
    """Append-only human-visible timeline for one Agent Computer session."""

    __tablename__ = "agent_computer_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_computer_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="recorded")
    capability_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_agent_computer_step_sequence"),
        Index("ix_agent_computer_step_scope", "tenant_id", "session_id", "sequence"),
    )
