from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from packages.database.db import Base


def uid() -> str:
    return str(uuid4())


class PluginPackageRecord(Base):
    __tablename__ = "plugin_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    namespace: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    plugin_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(30), nullable=False, default="private", index=True)
    owner_tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    publisher_user_id: Mapped[str | None] = mapped_column(ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("namespace", "plugin_id", name="uq_plugin_package_namespace_id"),)


class PluginVersionRecord(Base):
    __tablename__ = "plugin_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    package_id: Mapped[str] = mapped_column(ForeignKey("plugin_packages.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    package_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("operly_artifacts.id", ondelete="SET NULL"), nullable=True, index=True)
    sbom_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("operly_artifacts.id", ondelete="SET NULL"), nullable=True)
    source_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    trust_level: Mapped[str] = mapped_column(String(30), nullable=False, default="workspace_generated", index=True)
    validation_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    validation_report_json: Mapped[str] = mapped_column(Text, default="{}")
    signature_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("package_id", "version", name="uq_plugin_version_package_version"),
        UniqueConstraint("package_id", "manifest_digest", name="uq_plugin_version_manifest_digest"),
    )


class PluginInstallationRecord(Base):
    __tablename__ = "plugin_installations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    package_id: Mapped[str] = mapped_column(ForeignKey("plugin_packages.id", ondelete="CASCADE"), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("plugin_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="installed", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    configuration_json: Mapped[str] = mapped_column(Text, default="{}")
    granted_permissions_json: Mapped[str] = mapped_column(Text, default="[]")
    approved_network_json: Mapped[str] = mapped_column(Text, default="{}")
    installed_by: Mapped[str | None] = mapped_column(ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("tenant_id", "package_id", name="uq_plugin_installation_workspace_package"),)


class PluginRuntimeInstanceRecord(Base):
    __tablename__ = "plugin_runtime_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    installation_id: Mapped[str] = mapped_column(ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("plugin_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    runtime_profile: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    runtime_kind: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="provisioning", index=True)
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    endpoint_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("operly_artifacts.id", ondelete="SET NULL"), nullable=True)
    health_state: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown", index=True)
    health_evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PluginRuntimeIdentityRecord(Base):
    __tablename__ = "plugin_runtime_identities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    installation_id: Mapped[str] = mapped_column(ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=False, index=True)
    runtime_instance_id: Mapped[str | None] = mapped_column(ForeignKey("plugin_runtime_instances.id", ondelete="CASCADE"), nullable=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    allowed_bindings_json: Mapped[str] = mapped_column(Text, default="[]")
    issued_to: Mapped[str] = mapped_column(String(160), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PluginStorageNamespaceRecord(Base):
    __tablename__ = "plugin_storage_namespaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    installation_id: Mapped[str] = mapped_column(ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    storage_kind: Mapped[str] = mapped_column(String(30), nullable=False, default="kv")
    quota_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=10 * 1024 * 1024)
    used_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retention_policy_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("installation_id", "name", name="uq_plugin_storage_installation_name"),)


class DigitalEventOutboxRecord(Base):
    __tablename__ = "digital_event_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    source_kind: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    source_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    subject_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    subject_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    locked_by: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_digital_event_pending", "tenant_id", "status", "available_at"),)


class DigitalEventSubscriptionRecord(Base):
    __tablename__ = "digital_event_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    installation_id: Mapped[str | None] = mapped_column(ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=True, index=True)
    event_pattern: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    target_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    target_reference: Mapped[str] = mapped_column(String(240), nullable=False)
    secret_reference: Mapped[str | None] = mapped_column(ForeignKey("connector_secrets.id", ondelete="SET NULL"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    delivery_policy_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DigitalResourceBudgetRecord(Base):
    __tablename__ = "digital_resource_budgets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    hard_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    soft_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("tenant_id", "subject_kind", "subject_id", "metric", "window_seconds", name="uq_digital_resource_budget"),)
