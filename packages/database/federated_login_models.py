"""Single-use browser-bound authorization attempt for Operly's OIDC client."""
from datetime import datetime
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from packages.database.db import Base


class FederatedLoginAttempt(Base):
    __tablename__ = "federated_login_attempts"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
