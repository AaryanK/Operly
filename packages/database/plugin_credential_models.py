from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class PluginCredentialBindingRecord(Base):
    """Workspace-owned secret handle bound to one installed plugin declaration.

    The row stores only a reference to Operly's encrypted secret store. Hosted plugin
    code never receives ``connector_secrets`` access or a database credential.
    """

    __tablename__ = "plugin_credential_bindings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_name: Mapped[str] = mapped_column(String(120), nullable=False)
    credential_type: Mapped[str] = mapped_column(String(30), nullable=False)
    secret_reference: Mapped[str] = mapped_column(
        ForeignKey("connector_secrets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    granted_scopes_json: Mapped[str] = mapped_column(Text, default="[]")
    allowed_hosts_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active", index=True)
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "credential_name",
            name="uq_plugin_credential_installation_name",
        ),
    )


class PluginEgressGrantRecord(Base):
    """Narrow egress authorization used later by the trusted HTTP/credential broker."""

    __tablename__ = "plugin_egress_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_binding_id: Mapped[str | None] = mapped_column(
        ForeignKey("plugin_credential_bindings.id", ondelete="CASCADE"), nullable=True, index=True
    )
    host: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    methods_json: Mapped[str] = mapped_column(Text, default='["GET"]')
    path_prefixes_json: Mapped[str] = mapped_column(Text, default='["/"]')
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "credential_binding_id",
            "host",
            name="uq_plugin_egress_installation_credential_host",
        ),
    )
