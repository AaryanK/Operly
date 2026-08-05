"""architecture first software plans

Revision ID: 0006_architecture_first_plans
Revises: 0005_custom_software_vertical_slice
"""
from alembic import op
import sqlalchemy as sa

revision="0006_architecture_first_plans"
down_revision="0005_custom_software_vertical_slice"
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind();inspector=sa.inspect(bind);tables=set(inspector.get_table_names())
    if "software_plans" not in tables:
        op.create_table("software_plans",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("prompt",sa.Text(),nullable=False),sa.Column("current_version",sa.Integer(),nullable=False,server_default="1"),sa.Column("approved_version",sa.Integer(),nullable=True),sa.Column("status",sa.String(30),nullable=False,server_default="draft"),sa.Column("created_by",sa.String(36),sa.ForeignKey("app_users.id"),nullable=False),sa.Column("created_at",sa.DateTime(),nullable=False))
        op.create_index("ix_software_plans_tenant_id","software_plans",["tenant_id"]);op.create_index("ix_software_plans_status","software_plans",["status"])
    if "software_plan_versions" not in tables:
        op.create_table("software_plan_versions",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("plan_id",sa.String(36),sa.ForeignKey("software_plans.id",ondelete="CASCADE"),nullable=False),sa.Column("version",sa.Integer(),nullable=False),sa.Column("plan_json",sa.Text(),nullable=False),sa.Column("revision_request",sa.Text(),nullable=True),sa.Column("created_by",sa.String(36),sa.ForeignKey("app_users.id"),nullable=False),sa.Column("created_at",sa.DateTime(),nullable=False),sa.UniqueConstraint("plan_id","version",name="uq_software_plan_version"))
        op.create_index("ix_software_plan_versions_tenant_id","software_plan_versions",["tenant_id"]);op.create_index("ix_software_plan_versions_plan_id","software_plan_versions",["plan_id"])

def downgrade():
    raise RuntimeError("Software plan history downgrade is unsafe; restore a verified backup")
