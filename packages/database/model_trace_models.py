from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class ModelRuntimeTrace(Base):
    """Append-only model-runtime telemetry for one authenticated conversation.

    Payloads are provider-neutral request/response/error packets persisted by the
    tracing sink after credential-shaped values have been redacted. Conversation
    ownership is enforced by the API before any trace row is returned.
    """

    __tablename__ = "model_runtime_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    principal_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(40), nullable=True)
    surface: Mapped[str | None] = mapped_column(String(80), nullable=True)
    component: Mapped[str | None] = mapped_column(String(120), nullable=True)
    step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classification: Mapped[str | None] = mapped_column(String(80), nullable=True)
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index(
            "ix_model_runtime_trace_conversation_created",
            "conversation_id",
            "created_at",
        ),
        Index(
            "ix_model_runtime_trace_run_created",
            "run_id",
            "created_at",
        ),
    )
