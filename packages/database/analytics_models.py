from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class ProductAnalyticsEvent(Base):
    """Privacy-minimized first-party product analytics for the Operly web app.

    The event stores derived country information when a trusted edge proxy provides
    it, but never stores the request IP address. User and workspace references stay
    nullable so the schema can later support anonymous public-site analytics without
    changing the authenticated product event contract.
    """

    __tablename__ = "product_analytics_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tenant_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_name: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index(
            "ix_product_analytics_event_created",
            "event_name",
            "created_at",
        ),
        Index(
            "ix_product_analytics_country_created",
            "country_code",
            "created_at",
        ),
        Index(
            "ix_product_analytics_user_created",
            "user_id",
            "created_at",
        ),
    )
