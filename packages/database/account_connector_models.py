from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class AccountConnectorSecret(Base):
    """Encrypted credential owned by a person, never by a workspace."""

    __tablename__ = "account_connector_secrets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AccountConnector(Base):
    """Personal connector that may be delegated without changing ownership."""

    __tablename__ = "account_connectors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connector_type: Mapped[str] = mapped_column(String(60), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="connected", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    credential_reference: Mapped[str | None] = mapped_column(
        ForeignKey("account_connector_secrets.id", ondelete="SET NULL"), nullable=True
    )
    provider_account_id: Mapped[str | None] = mapped_column(String(320), nullable=True)
    granted_scopes_json: Mapped[str] = mapped_column(Text, default="[]")
    configuration_json: Mapped[str] = mapped_column(Text, default="{}")
    health_status: Mapped[str] = mapped_column(String(40), default="unknown")
    last_health_check: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "provider", "provider_account_id", name="uq_account_connector_account"),
        Index("ix_account_connector_user_provider", "user_id", "provider", "enabled", "status"),
    )
