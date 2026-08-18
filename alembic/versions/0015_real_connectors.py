"""real tenant connectors
Revision ID: 0015_real_connectors
Revises: 0014_plugin_harness
"""
from alembic import op
import sqlalchemy as sa
revision="0015_real_connectors";down_revision="0014_plugin_harness";branch_labels=None;depends_on=None
def upgrade():
 existing=set(sa.inspect(op.get_bind()).get_table_names())
 if "connector_secrets" not in existing:op.create_table("connector_secrets",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("ciphertext",sa.Text,nullable=False),sa.Column("created_at",sa.DateTime),sa.Column("updated_at",sa.DateTime));op.create_index("ix_connector_secrets_tenant_id","connector_secrets",["tenant_id"])
 if "tenant_connectors" not in existing:op.create_table("tenant_connectors",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("connector_type",sa.String(60),nullable=False),sa.Column("provider",sa.String(60),nullable=False),sa.Column("display_name",sa.String(200),nullable=False),sa.Column("status",sa.String(40),nullable=False),sa.Column("enabled",sa.Boolean,nullable=False),sa.Column("credential_reference",sa.String(36),sa.ForeignKey("connector_secrets.id")),sa.Column("provider_account_id",sa.String(320)),sa.Column("granted_scopes_json",sa.Text,nullable=False),sa.Column("configuration_json",sa.Text,nullable=False),sa.Column("health_status",sa.String(40),nullable=False),sa.Column("last_health_check",sa.DateTime),sa.Column("last_error",sa.Text),sa.Column("created_at",sa.DateTime),sa.Column("updated_at",sa.DateTime),sa.UniqueConstraint("tenant_id","provider","provider_account_id",name="uq_tenant_connector_account"));op.create_index("ix_tenant_connectors_tenant_id","tenant_connectors",["tenant_id"]);op.create_index("ix_tenant_connectors_status","tenant_connectors",["status"]);op.create_index("ix_tenant_connectors_enabled","tenant_connectors",["enabled"])
 columns={x["name"] for x in sa.inspect(op.get_bind()).get_columns("business_actions")}
 if "approved_arguments_digest" not in columns:
  with op.batch_alter_table("business_actions") as b:b.add_column(sa.Column("approved_arguments_digest",sa.String(64),nullable=True))
def downgrade():
 with op.batch_alter_table("business_actions") as b:b.drop_column("approved_arguments_digest")
 op.drop_table("tenant_connectors");op.drop_table("connector_secrets")
