from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class WorkspaceRole(Base):
    __tablename__ = "workspace_roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_workspace_role_tenant_key"),
        Index("ix_workspace_role_tenant_name", "tenant_id", "name"),
    )


class WorkspaceRolePermission(Base):
    __tablename__ = "workspace_role_permissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    role_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    permission: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("role_id", "permission", name="uq_workspace_role_permission"),
    )
