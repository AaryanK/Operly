"""digital business plugin/runtime infrastructure baseline

Revision ID: 0053_digital_business_infrastructure
Revises: 0052_mcp_agent_gateway
"""

from alembic import op
import sqlalchemy as sa

revision = "0053_digital_business_infrastructure"
down_revision = "0052_mcp_agent_gateway"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("plugin_packages"):
        return

    op.create_table(
        "plugin_packages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("namespace", sa.String(180), nullable=False),
        sa.Column("plugin_id", sa.String(120), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("visibility", sa.String(30), nullable=False, server_default="private"),
        sa.Column("owner_tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("publisher_user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("namespace", "plugin_id", name="uq_plugin_package_namespace_id"),
    )
    op.create_index("ix_plugin_packages_namespace", "plugin_packages", ["namespace"])
    op.create_index("ix_plugin_packages_plugin_id", "plugin_packages", ["plugin_id"])
    op.create_index("ix_plugin_packages_owner_tenant_id", "plugin_packages", ["owner_tenant_id"])

    op.create_table(
        "plugin_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("package_id", sa.String(36), sa.ForeignKey("plugin_packages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("package_artifact_id", sa.String(36), sa.ForeignKey("operly_artifacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sbom_artifact_id", sa.String(36), sa.ForeignKey("operly_artifacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_digest", sa.String(64), nullable=True),
        sa.Column("trust_level", sa.String(30), nullable=False, server_default="workspace_generated"),
        sa.Column("validation_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("validation_report_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("signature_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("package_id", "version", name="uq_plugin_version_package_version"),
        sa.UniqueConstraint("package_id", "manifest_digest", name="uq_plugin_version_manifest_digest"),
    )
    for column in ("package_id", "manifest_digest", "package_artifact_id", "source_digest", "trust_level", "validation_status"):
        op.create_index(f"ix_plugin_versions_{column}", "plugin_versions", [column])

    op.create_table(
        "plugin_installations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("package_id", sa.String(36), sa.ForeignKey("plugin_packages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("plugin_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="installed"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("configuration_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("granted_permissions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("approved_network_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("installed_by", sa.String(36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("installed_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "package_id", name="uq_plugin_installation_workspace_package"),
    )
    for column in ("tenant_id", "package_id", "version_id", "status", "enabled"):
        op.create_index(f"ix_plugin_installations_{column}", "plugin_installations", [column])

    op.create_table(
        "plugin_runtime_instances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("installation_id", sa.String(36), sa.ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("plugin_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("runtime_profile", sa.String(120), nullable=False),
        sa.Column("runtime_kind", sa.String(30), nullable=False),
        sa.Column("state", sa.String(30), nullable=False, server_default="provisioning"),
        sa.Column("provider", sa.String(60), nullable=True),
        sa.Column("provider_reference", sa.String(200), nullable=True),
        sa.Column("endpoint_reference", sa.Text(), nullable=True),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("operly_artifacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("health_state", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("health_evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ("tenant_id", "installation_id", "version_id", "runtime_profile", "runtime_kind", "state", "provider_reference", "health_state", "last_heartbeat_at", "expires_at"):
        op.create_index(f"ix_plugin_runtime_instances_{column}", "plugin_runtime_instances", [column])

    op.create_table(
        "plugin_runtime_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("installation_id", sa.String(36), sa.ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("runtime_instance_id", sa.String(36), sa.ForeignKey("plugin_runtime_instances.id", ondelete="CASCADE"), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("allowed_bindings_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("issued_to", sa.String(160), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("tenant_id", "installation_id", "runtime_instance_id", "token_hash", "expires_at", "revoked_at"):
        op.create_index(f"ix_plugin_runtime_identities_{column}", "plugin_runtime_identities", [column])

    op.create_table(
        "plugin_storage_namespaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("installation_id", sa.String(36), sa.ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("storage_kind", sa.String(30), nullable=False, server_default="kv"),
        sa.Column("quota_bytes", sa.Integer(), nullable=False),
        sa.Column("used_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retention_policy_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("installation_id", "name", name="uq_plugin_storage_installation_name"),
    )
    op.create_index("ix_plugin_storage_namespaces_tenant_id", "plugin_storage_namespaces", ["tenant_id"])
    op.create_index("ix_plugin_storage_namespaces_installation_id", "plugin_storage_namespaces", ["installation_id"])

    op.create_table(
        "plugin_kv_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("installation_id", sa.String(36), sa.ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("namespace_id", sa.String(36), sa.ForeignKey("plugin_storage_namespaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(500), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("namespace_id", "key", name="uq_plugin_kv_namespace_key"),
    )
    for column in ("tenant_id", "installation_id", "namespace_id"):
        op.create_index(f"ix_plugin_kv_records_{column}", "plugin_kv_records", [column])

    op.create_table(
        "plugin_blob_references",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("installation_id", sa.String(36), sa.ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("namespace_id", sa.String(36), sa.ForeignKey("plugin_storage_namespaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("logical_name", sa.String(500), nullable=False),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("operly_artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("namespace_id", "logical_name", name="uq_plugin_blob_namespace_name"),
    )
    for column in ("tenant_id", "installation_id", "namespace_id", "artifact_id"):
        op.create_index(f"ix_plugin_blob_references_{column}", "plugin_blob_references", [column])

    op.create_table(
        "digital_event_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(180), nullable=False),
        sa.Column("source_kind", sa.String(60), nullable=False),
        sa.Column("source_id", sa.String(160), nullable=True),
        sa.Column("subject_type", sa.String(100), nullable=True),
        sa.Column("subject_id", sa.String(160), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("locked_by", sa.String(160), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_digital_event_pending", "digital_event_outbox", ["tenant_id", "status", "available_at"])
    for column in ("event_type", "source_kind", "source_id", "subject_type", "subject_id", "status", "available_at", "locked_by", "lease_expires_at", "created_at"):
        op.create_index(f"ix_digital_event_outbox_{column}", "digital_event_outbox", [column])

    op.create_table(
        "digital_event_subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("installation_id", sa.String(36), sa.ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("event_pattern", sa.String(180), nullable=False),
        sa.Column("target_kind", sa.String(40), nullable=False),
        sa.Column("target_reference", sa.String(240), nullable=False),
        sa.Column("secret_reference", sa.String(36), sa.ForeignKey("connector_secrets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("delivery_policy_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ("tenant_id", "installation_id", "event_pattern", "target_kind", "enabled"):
        op.create_index(f"ix_digital_event_subscriptions_{column}", "digital_event_subscriptions", [column])

    op.create_table(
        "digital_resource_budgets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_kind", sa.String(40), nullable=False),
        sa.Column("subject_id", sa.String(160), nullable=False),
        sa.Column("metric", sa.String(80), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("hard_limit", sa.Integer(), nullable=False),
        sa.Column("soft_limit", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "subject_kind", "subject_id", "metric", "window_seconds", name="uq_digital_resource_budget"),
    )
    for column in ("tenant_id", "subject_kind", "subject_id"):
        op.create_index(f"ix_digital_resource_budgets_{column}", "digital_resource_budgets", [column])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    for table in (
        "digital_resource_budgets",
        "digital_event_subscriptions",
        "digital_event_outbox",
        "plugin_blob_references",
        "plugin_kv_records",
        "plugin_storage_namespaces",
        "plugin_runtime_identities",
        "plugin_runtime_instances",
        "plugin_installations",
        "plugin_versions",
        "plugin_packages",
    ):
        if inspector.has_table(table):
            op.drop_table(table)
