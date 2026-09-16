from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class AgentSpendBudgetRecord(Base):
    """Durable conservative spend bucket for one trusted budget dimension."""

    __tablename__ = "agent_spend_budgets"
    __table_args__ = (
        UniqueConstraint(
            "scope_kind",
            "scope_id",
            "budget_kind",
            "budget_key",
            name="uq_agent_spend_budget_scope_key",
        ),
        CheckConstraint("limit_micros >= 0", name="ck_agent_spend_budget_limit_nonnegative"),
        CheckConstraint("spent_micros >= 0", name="ck_agent_spend_budget_spent_nonnegative"),
        CheckConstraint("reserved_micros >= 0", name="ck_agent_spend_budget_reserved_nonnegative"),
        CheckConstraint("calls_used >= 0", name="ck_agent_spend_budget_calls_nonnegative"),
        Index(
            "ix_agent_spend_budget_scope_kind_key",
            "scope_kind",
            "scope_id",
            "budget_kind",
            "budget_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    budget_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    budget_key: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    limit_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spent_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    max_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AgentSpendReservationRecord(Base):
    """One pre-dispatch monetary reservation and its eventual usage evidence."""

    __tablename__ = "agent_spend_reservations"
    __table_args__ = (
        UniqueConstraint("reservation_key", name="uq_agent_spend_reservation_key"),
        CheckConstraint("reserved_micros >= 0", name="ck_agent_spend_reservation_reserved_nonnegative"),
        CheckConstraint(
            "actual_micros IS NULL OR actual_micros >= 0",
            name="ck_agent_spend_reservation_actual_nonnegative",
        ),
        Index("ix_agent_spend_reservation_run_created", "run_id", "created_at"),
        Index("ix_agent_spend_reservation_scope_created", "scope_kind", "scope_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    reservation_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    price_snapshot_version: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    input_micros_per_million_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_micros_per_million_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_token_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    output_token_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="reserved", index=True)
    budget_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    uncertainty_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
