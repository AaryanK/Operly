from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class KernelRun(Base):
    __tablename__ = "kernel_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    principal_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    surface: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    conversation_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    capability_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class KernelRequestClaim(Base):
    """Scoped idempotency reservation for externally retryable Kernel requests."""

    __tablename__ = "kernel_request_claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    idempotency_key: Mapped[str] = mapped_column(String(400), nullable=False, unique=True, index=True)
    request_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    principal_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    capability_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running", index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    response_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False, index=True
    )


class KernelApproval(Base):
    """Durable, argument-bound approval for one governed capability invocation."""

    __tablename__ = "kernel_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    requested_by_principal_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    requested_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    source_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    decided_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    consumed_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class KernelRunStep(Base):
    __tablename__ = "kernel_run_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("kernel_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class KernelEventRecord(Base):
    __tablename__ = "kernel_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    principal_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False, default="system")
    actor_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    initiator_principal_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    executor_principal_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    capability_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
