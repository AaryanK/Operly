"""Add source-backed field-service vertical slice.

Revision ID: 0005_custom_software_vertical_slice
Revises: 0004_managed_record_runtime
"""
from alembic import op
import sqlalchemy as sa

revision="0005_custom_software_vertical_slice"
down_revision="0004_managed_record_runtime"
branch_labels=None
depends_on=None


def upgrade():
    op.create_table("generated_projects",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("slug",sa.String(100),nullable=False),sa.Column("name",sa.String(200),nullable=False),sa.Column("vertical",sa.String(50),nullable=False),sa.Column("prompt",sa.Text(),nullable=False),sa.Column("brand_json",sa.Text(),nullable=False),sa.Column("artifact_graph_json",sa.Text(),nullable=False),sa.Column("version",sa.Integer(),nullable=False,server_default="1"),sa.Column("created_by",sa.String(36),sa.ForeignKey("app_users.id"),nullable=False),sa.Column("created_at",sa.DateTime(),nullable=False,server_default=sa.text("CURRENT_TIMESTAMP")),sa.UniqueConstraint("slug",name="uq_generated_project_slug"))
    op.create_index("ix_generated_projects_tenant_id","generated_projects",["tenant_id"])
    op.create_table("generated_project_change_sets",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("project_id",sa.String(36),sa.ForeignKey("generated_projects.id",ondelete="CASCADE"),nullable=False),sa.Column("base_version",sa.Integer(),nullable=False),sa.Column("request",sa.Text(),nullable=False),sa.Column("selected_artifacts_json",sa.Text(),nullable=False,server_default="[]"),sa.Column("before_json",sa.Text(),nullable=False),sa.Column("after_json",sa.Text(),nullable=False),sa.Column("impact_json",sa.Text(),nullable=False),sa.Column("status",sa.String(30),nullable=False,server_default="proposed"),sa.Column("created_by",sa.String(36),sa.ForeignKey("app_users.id"),nullable=False),sa.Column("created_at",sa.DateTime(),nullable=False,server_default=sa.text("CURRENT_TIMESTAMP")))
    for column in ("tenant_id","project_id","status"):op.create_index(f"ix_generated_project_change_sets_{column}","generated_project_change_sets",[column])
    op.create_table("service_customers",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("name",sa.String(160),nullable=False),sa.Column("phone",sa.String(40),nullable=False),sa.Column("email",sa.String(320),nullable=True),sa.Column("created_at",sa.DateTime(),nullable=False,server_default=sa.text("CURRENT_TIMESTAMP")))
    op.create_index("ix_service_customers_tenant_id","service_customers",["tenant_id"])
    op.create_table("service_requests",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("project_id",sa.String(36),sa.ForeignKey("generated_projects.id",ondelete="CASCADE"),nullable=False),sa.Column("customer_id",sa.String(36),sa.ForeignKey("service_customers.id"),nullable=False),sa.Column("reference",sa.String(20),nullable=False,unique=True),sa.Column("idempotency_key",sa.String(120),nullable=False),sa.Column("issue_category",sa.String(80),nullable=False),sa.Column("description",sa.Text(),nullable=False,server_default=""),sa.Column("address",sa.String(500),nullable=False),sa.Column("asset_details",sa.String(500),nullable=False,server_default=""),sa.Column("status",sa.String(30),nullable=False,server_default="submitted"),sa.Column("assigned_to",sa.String(160),nullable=True),sa.Column("version",sa.Integer(),nullable=False,server_default="1"),sa.Column("created_at",sa.DateTime(),nullable=False,server_default=sa.text("CURRENT_TIMESTAMP")),sa.Column("updated_at",sa.DateTime(),nullable=False,server_default=sa.text("CURRENT_TIMESTAMP")),sa.UniqueConstraint("tenant_id","project_id","idempotency_key",name="uq_service_request_idempotency"))
    for column in ("tenant_id","project_id","customer_id","reference","status"):op.create_index(f"ix_service_requests_{column}","service_requests",[column])
    op.create_table("service_status_events",sa.Column("id",sa.String(36),primary_key=True),sa.Column("tenant_id",sa.String(36),sa.ForeignKey("tenants.id"),nullable=False),sa.Column("request_id",sa.String(36),sa.ForeignKey("service_requests.id",ondelete="CASCADE"),nullable=False),sa.Column("from_status",sa.String(30),nullable=True),sa.Column("to_status",sa.String(30),nullable=False),sa.Column("actor_id",sa.String(120),nullable=True),sa.Column("note",sa.String(500),nullable=False,server_default=""),sa.Column("created_at",sa.DateTime(),nullable=False,server_default=sa.text("CURRENT_TIMESTAMP")))
    op.create_index("ix_service_status_events_tenant_id","service_status_events",["tenant_id"]);op.create_index("ix_service_status_events_request_id","service_status_events",["request_id"])


def downgrade():
    raise RuntimeError("Custom software data downgrade is unsafe; restore a verified backup")
