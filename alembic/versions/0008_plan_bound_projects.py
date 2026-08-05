"""bind generated projects to approved software plans

Revision ID: 0008_plan_bound_projects
Revises: 0007_architecture_pack_runtimes
"""
from alembic import op
import sqlalchemy as sa
revision="0008_plan_bound_projects";down_revision="0007_architecture_pack_runtimes";branch_labels=None;depends_on=None
def upgrade():
    bind=op.get_bind();inspector=sa.inspect(bind)
    columns={item["name"] for item in inspector.get_columns("generated_projects")}
    indexes={item["name"] for item in inspector.get_indexes("generated_projects")}
    foreign_keys={item.get("name") for item in inspector.get_foreign_keys("generated_projects")}
    with op.batch_alter_table("generated_projects") as batch:
        if "plan_id" not in columns:batch.add_column(sa.Column("plan_id",sa.String(36),nullable=True))
        if "approved_plan_version" not in columns:batch.add_column(sa.Column("approved_plan_version",sa.Integer(),nullable=True))
        if "architecture_pack" not in columns:batch.add_column(sa.Column("architecture_pack",sa.String(50),nullable=False,server_default="field_service"))
        if "fk_generated_projects_plan" not in foreign_keys:batch.create_foreign_key("fk_generated_projects_plan","software_plans",["plan_id"],["id"])
        if "ix_generated_projects_plan_id" not in indexes:batch.create_index("ix_generated_projects_plan_id",["plan_id"])
        if "ix_generated_projects_architecture_pack" not in indexes:batch.create_index("ix_generated_projects_architecture_pack",["architecture_pack"])
def downgrade():raise RuntimeError("Approved-plan traceability downgrade is unsafe; restore a verified backup")
