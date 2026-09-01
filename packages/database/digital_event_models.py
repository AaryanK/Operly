from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class DigitalEventDeliveryRecord(Base):
    """Durable per-subscription delivery state for one outbox event."""

    __tablename__ = "digital_event_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("digital_event_outbox.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("digital_event_subscriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    locked_by: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("event_id", "subscription_id", name="uq_digital_event_delivery_event_subscription"),
    )


class DigitalWebhookEndpointRecord(Base):
    """Inbound webhook endpoint metadata; secrets stay in ConnectorSecret."""

    __tablename__ = "digital_webhook_endpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    installation_id: Mapped[str | None] = mapped_column(
        ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    endpoint_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    verification_type: Mapped[str] = mapped_column(String(40), nullable=False, default="none")
    secret_reference: Mapped[str | None] = mapped_column(
        ForeignKey("connector_secrets.id", ondelete="SET NULL"), nullable=True
    )
    max_body_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=1024 * 1024)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class DigitalWebhookReceiptRecord(Base):
    """Deduplicated inbound webhook evidence; raw payload may be stored as an artifact."""

    __tablename__ = "digital_webhook_receipts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    endpoint_id: Mapped[str] = mapped_column(
        ForeignKey("digital_webhook_endpoints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dedupe_key: Mapped[str] = mapped_column(String(240), nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("operly_artifacts.id", ondelete="SET NULL"), nullable=True
    )
    headers_json: Mapped[str] = mapped_column(Text, default="{}")
    verification_state: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    processing_state: Mapped[str] = mapped_column(String(30), nullable=False, default="received", index=True)
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("digital_event_outbox.id", ondelete="SET NULL"), nullable=True, index=True
    )
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("endpoint_id", "dedupe_key", name="uq_digital_webhook_endpoint_dedupe"),
    )
