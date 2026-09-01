"""digital runtime support primitives

Revision ID: 0054_digital_runtime_support
Revises: 0053_digital_business_infrastructure
"""

from alembic import op
import sqlalchemy as sa

revision = "0054_digital_runtime_support"
down_revision = "0053_digital_business_infrastructure"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("capability_bindings"):
        op.create_table(
            "capability_bindings",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subject_kind", sa.String(40), nullable=False),
            sa.Column("subject_id", sa.String(160), nullable=False),
            sa.Column("semantic_name", sa.String(160), nullable=False),
            sa.Column("capability_id", sa.String(200), nullable=False),
            sa.Column("capability_version", sa.String(80), nullable=False),
            sa.Column("authority_user_id", sa.String(36), sa.ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("configuration_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("argument_constraints_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("rate_policy_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(30), nullable=False, server_default="active"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "subject_kind",
                "subject_id",
                "semantic_name",
                name="uq_capability_binding_subject_semantic",
            ),
        )
        for column in (
            "tenant_id",
            "subject_kind",
            "subject_id",
            "capability_id",
            "authority_user_id",
            "status",
            "enabled",
            "created_by",
        ):
            op.create_index(f"ix_capability_bindings_{column}", "capability_bindings", [column])

    if not inspector.has_table("plugin_credential_bindings"):
        op.create_table(
            "plugin_credential_bindings",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("installation_id", sa.String(36), sa.ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("credential_name", sa.String(120), nullable=False),
            sa.Column("credential_type", sa.String(30), nullable=False),
            sa.Column("secret_reference", sa.String(36), sa.ForeignKey("connector_secrets.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("granted_scopes_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("allowed_hosts_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(30), nullable=False, server_default="active"),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "installation_id",
                "credential_name",
                name="uq_plugin_credential_installation_name",
            ),
        )
        for column in ("tenant_id", "installation_id", "secret_reference", "status", "created_by"):
            op.create_index(f"ix_plugin_credential_bindings_{column}", "plugin_credential_bindings", [column])

    if not inspector.has_table("plugin_egress_grants"):
        op.create_table(
            "plugin_egress_grants",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("installation_id", sa.String(36), sa.ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("credential_binding_id", sa.String(36), sa.ForeignKey("plugin_credential_bindings.id", ondelete="CASCADE"), nullable=True),
            sa.Column("host", sa.String(253), nullable=False),
            sa.Column("methods_json", sa.Text(), nullable=False, server_default='["GET"]'),
            sa.Column("path_prefixes_json", sa.Text(), nullable=False, server_default='["/"]'),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "installation_id",
                "credential_binding_id",
                "host",
                name="uq_plugin_egress_installation_credential_host",
            ),
        )
        for column in ("tenant_id", "installation_id", "credential_binding_id", "host", "enabled"):
            op.create_index(f"ix_plugin_egress_grants_{column}", "plugin_egress_grants", [column])

    if not inspector.has_table("digital_event_deliveries"):
        op.create_table(
            "digital_event_deliveries",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event_id", sa.String(36), sa.ForeignKey("digital_event_outbox.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subscription_id", sa.String(36), sa.ForeignKey("digital_event_subscriptions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("available_at", sa.DateTime(), nullable=False),
            sa.Column("locked_by", sa.String(160), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("response_status", sa.Integer(), nullable=True),
            sa.Column("response_digest", sa.String(64), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("delivered_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint(
                "event_id",
                "subscription_id",
                name="uq_digital_event_delivery_event_subscription",
            ),
        )
        for column in (
            "tenant_id",
            "event_id",
            "subscription_id",
            "status",
            "available_at",
            "locked_by",
            "lease_expires_at",
        ):
            op.create_index(f"ix_digital_event_deliveries_{column}", "digital_event_deliveries", [column])

    if not inspector.has_table("digital_webhook_endpoints"):
        op.create_table(
            "digital_webhook_endpoints",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("installation_id", sa.String(36), sa.ForeignKey("plugin_installations.id", ondelete="CASCADE"), nullable=True),
            sa.Column("endpoint_key_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("event_type", sa.String(180), nullable=False),
            sa.Column("verification_type", sa.String(40), nullable=False, server_default="none"),
            sa.Column("secret_reference", sa.String(36), sa.ForeignKey("connector_secrets.id", ondelete="SET NULL"), nullable=True),
            sa.Column("max_body_bytes", sa.Integer(), nullable=False, server_default=str(1024 * 1024)),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        for column in ("tenant_id", "installation_id", "endpoint_key_hash", "event_type", "enabled"):
            op.create_index(f"ix_digital_webhook_endpoints_{column}", "digital_webhook_endpoints", [column])

    if not inspector.has_table("digital_webhook_receipts"):
        op.create_table(
            "digital_webhook_receipts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("endpoint_id", sa.String(36), sa.ForeignKey("digital_webhook_endpoints.id", ondelete="CASCADE"), nullable=False),
            sa.Column("dedupe_key", sa.String(240), nullable=False),
            sa.Column("body_sha256", sa.String(64), nullable=False),
            sa.Column("payload_artifact_id", sa.String(36), sa.ForeignKey("operly_artifacts.id", ondelete="SET NULL"), nullable=True),
            sa.Column("headers_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("verification_state", sa.String(30), nullable=False, server_default="pending"),
            sa.Column("processing_state", sa.String(30), nullable=False, server_default="received"),
            sa.Column("event_id", sa.String(36), sa.ForeignKey("digital_event_outbox.id", ondelete="SET NULL"), nullable=True),
            sa.Column("received_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "endpoint_id",
                "dedupe_key",
                name="uq_digital_webhook_endpoint_dedupe",
            ),
        )
        for column in (
            "tenant_id",
            "endpoint_id",
            "body_sha256",
            "verification_state",
            "processing_state",
            "event_id",
            "received_at",
        ):
            op.create_index(f"ix_digital_webhook_receipts_{column}", "digital_webhook_receipts", [column])

    if not inspector.has_table("digital_platform_jobs"):
        op.create_table(
            "digital_platform_jobs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
            sa.Column("job_type", sa.String(100), nullable=False),
            sa.Column("subject_kind", sa.String(60), nullable=False),
            sa.Column("subject_id", sa.String(160), nullable=False),
            sa.Column("state", sa.String(30), nullable=False, server_default="queued"),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("idempotency_key", sa.String(200), nullable=False),
            sa.Column("available_at", sa.DateTime(), nullable=False),
            sa.Column("locked_by", sa.String(160), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint(
                "tenant_id",
                "idempotency_key",
                name="uq_digital_platform_job_idempotency",
            ),
        )
        for column in (
            "tenant_id",
            "job_type",
            "subject_kind",
            "subject_id",
            "state",
            "priority",
            "available_at",
            "locked_by",
            "lease_expires_at",
            "created_by",
            "created_at",
        ):
            op.create_index(f"ix_digital_platform_jobs_{column}", "digital_platform_jobs", [column])
        op.create_index(
            "ix_digital_platform_job_dispatch",
            "digital_platform_jobs",
            ["state", "available_at", "priority", "created_at"],
        )

    if not inspector.has_table("digital_usage_buckets"):
        op.create_table(
            "digital_usage_buckets",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subject_kind", sa.String(40), nullable=False),
            sa.Column("subject_id", sa.String(160), nullable=False),
            sa.Column("metric", sa.String(80), nullable=False),
            sa.Column("window_start", sa.DateTime(), nullable=False),
            sa.Column("window_seconds", sa.Integer(), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "subject_kind",
                "subject_id",
                "metric",
                "window_start",
                "window_seconds",
                name="uq_digital_usage_bucket",
            ),
        )
        for column in ("tenant_id", "subject_kind", "subject_id", "metric", "window_start"):
            op.create_index(f"ix_digital_usage_buckets_{column}", "digital_usage_buckets", [column])
        op.create_index(
            "ix_digital_usage_subject_window",
            "digital_usage_buckets",
            ["tenant_id", "subject_kind", "subject_id", "window_start"],
        )

    if not inspector.has_table("digital_usage_ledger"):
        op.create_table(
            "digital_usage_ledger",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subject_kind", sa.String(40), nullable=False),
            sa.Column("subject_id", sa.String(160), nullable=False),
            sa.Column("metric", sa.String(80), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("reference_kind", sa.String(60), nullable=True),
            sa.Column("reference_id", sa.String(160), nullable=True),
            sa.Column("recorded_at", sa.DateTime(), nullable=False),
        )
        for column in (
            "tenant_id",
            "subject_kind",
            "subject_id",
            "metric",
            "reference_kind",
            "reference_id",
            "recorded_at",
        ):
            op.create_index(f"ix_digital_usage_ledger_{column}", "digital_usage_ledger", [column])
        op.create_index(
            "ix_digital_usage_ledger_subject_time",
            "digital_usage_ledger",
            ["tenant_id", "subject_kind", "subject_id", "recorded_at"],
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    for table in (
        "digital_usage_ledger",
        "digital_usage_buckets",
        "digital_platform_jobs",
        "digital_webhook_receipts",
        "digital_webhook_endpoints",
        "digital_event_deliveries",
        "plugin_egress_grants",
        "plugin_credential_bindings",
        "capability_bindings",
    ):
        if inspector.has_table(table):
            op.drop_table(table)
