from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class BusinessEventRecord(Base):
    __tablename__ = "business_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(40), default="system", nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source: Mapped[str] = mapped_column(String(100), default="operly", nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    causation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    __table_args__ = (Index("ix_business_events_tenant_type_time", "tenant_id", "event_type", "occurred_at"),)


class BusinessActionRecord(Base):
    __tablename__ = "business_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    capability: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    arguments_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    expected_outcome: Mapped[str] = mapped_column(Text, default="", nullable=False)
    risk_level: Mapped[str] = mapped_column(String(30), default="low", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="PROPOSED", nullable=False, index=True)
    policy_decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approvals.id"), nullable=True, index=True)
    result_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    verification_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(36), default=uid, nullable=False, index=True)
    causation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


@event.listens_for(BusinessEventRecord, "before_update")
@event.listens_for(BusinessEventRecord, "before_delete")
def _immutable_business_event(mapper, connection, target):
    del mapper, connection, target
    raise ValueError("Business events are append-only and immutable")
