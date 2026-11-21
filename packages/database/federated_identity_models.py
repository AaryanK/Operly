"""Read-only federation mapping; Operly retains local workspace user IDs."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


class FederatedIdentityMap(Base):
    __tablename__ = "federated_identity_maps"

    issuer: Mapped[str] = mapped_column(String(255), primary_key=True)
    subject: Mapped[str] = mapped_column(String(160), primary_key=True)
    operly_user_id: Mapped[str] = mapped_column(
        ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("operly_user_id", name="uq_federated_identity_operly_user"),
        Index("ix_federated_identity_operly_user_id", "operly_user_id"),
        CheckConstraint("status IN ('pending', 'active', 'disabled')", name="ck_federated_identity_status"),
    )
