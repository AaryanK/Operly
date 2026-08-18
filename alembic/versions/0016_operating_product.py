"""operating product records
Revision ID: 0016_operating_product
Revises: 0015_real_connectors
"""
from alembic import op
import sqlalchemy as sa
revision="0016_operating_product";down_revision="0015_real_connectors";branch_labels=None;depends_on=None
def upgrade():
 bind=op.get_bind();inspect=sa.inspect(bind);tables=set(inspect.get_table_names())
 columns={x["name"] for x in inspect.get_columns("leads")}
 with op.batch_alter_table("leads") as b:
  for name in ("last_activity_at","last_contacted_at","next_action_at","stage_changed_at"):
   if name not in columns:b.add_column(sa.Column(name,sa.DateTime(),nullable=True))
 if "payment_records" not in tables:op.create_table("payment_records",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("kind",sa.String(40),nullable=False),sa.Column("provider",sa.String(60),nullable=False),sa.Column("provider_id",sa.String(200),nullable=False),sa.Column("status",sa.String(50),nullable=False),sa.Column("amount",sa.Float,nullable=False),sa.Column("currency",sa.String(3),nullable=False),sa.Column("customer_email",sa.String(320)),sa.Column("description",sa.Text,nullable=False),sa.Column("url",sa.Text),sa.Column("metadata_json",sa.Text,nullable=False),sa.Column("created_at",sa.DateTime),sa.Column("updated_at",sa.DateTime),sa.UniqueConstraint("tenant_id","provider","provider_id",name="uq_payment_provider_id"));op.create_index("ix_payment_records_tenant_id","payment_records",["tenant_id"])
 if "custom_plugins" not in tables:op.create_table("custom_plugins",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("plugin_id",sa.String(100),nullable=False),sa.Column("display_name",sa.String(200),nullable=False),sa.Column("description",sa.Text,nullable=False),sa.Column("base_url",sa.Text,nullable=False),sa.Column("allowed_domain",sa.String(255),nullable=False),sa.Column("auth_type",sa.String(30),nullable=False),sa.Column("credential_reference",sa.String(36),sa.ForeignKey("connector_secrets.id")),sa.Column("capabilities_json",sa.Text,nullable=False),sa.Column("status",sa.String(40),nullable=False),sa.Column("enabled",sa.Boolean,nullable=False),sa.Column("test_results_json",sa.Text,nullable=False),sa.Column("created_at",sa.DateTime),sa.Column("updated_at",sa.DateTime),sa.UniqueConstraint("tenant_id","plugin_id",name="uq_custom_plugin_tenant_id"));op.create_index("ix_custom_plugins_tenant_id","custom_plugins",["tenant_id"])
 if "company_profiles" not in tables:op.create_table("company_profiles",sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),primary_key=True),sa.Column("answers_json",sa.Text,nullable=False),sa.Column("completed_at",sa.DateTime),sa.Column("updated_at",sa.DateTime))
def downgrade():pass
