from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class CapabilityBindingRecord(Base):
    """Universal delegated handle from a digital workload to one Operly capability.

    The binding is subject-based instead of project-only so generated Solutions,
    plugin installations, workers and future workloads share one gateway contract.
    ``authority_user_id`` identifies the human whose *current* Workspace authority is
    re-resolved on every invocation. The binding never freezes role permissions and
    never contains provider credentials.
    """

    __tablename__ = "capability_bindings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    semantic_name: Mapped[str] = mapped_column(String(160), nullable=False)
    capability_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    capability_version: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_user_id: Mapped[str] = mapped_column(
        ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    configuration_json: Mapped[str] = mapped_column(Text, default="{}")
    argument_constraints_json: Mapped[str] = mapped_column(Text, default="{}")
    rate_policy_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "subject_kind",
            "subject_id",
            "semantic_name",
            name="uq_capability_binding_subject_semantic",
        ),
    )
