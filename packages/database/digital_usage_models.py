from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class DigitalUsageBucketRecord(Base):
    """Atomic usage counter for one budget window."""

    __tablename__ = "digital_usage_buckets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "subject_kind",
            "subject_id",
            "metric",
            "window_start",
            "window_seconds",
            name="uq_digital_usage_bucket",
        ),
        Index(
            "ix_digital_usage_subject_window",
            "tenant_id",
            "subject_kind",
            "subject_id",
            "window_start",
        ),
    )


class DigitalUsageLedgerRecord(Base):
    """Append-only metering evidence for audit, billing and capacity analysis."""

    __tablename__ = "digital_usage_ledger"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_kind: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    reference_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index(
            "ix_digital_usage_ledger_subject_time",
            "tenant_id",
            "subject_kind",
            "subject_id",
            "recorded_at",
        ),
    )
