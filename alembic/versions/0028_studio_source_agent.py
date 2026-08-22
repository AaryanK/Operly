"""add immutable Studio source-agent versions

Revision ID: 0028_studio_source_agent
Revises: 0027_global_human_memory
"""

from alembic import op
import sqlalchemy as sa

revision = "0028_studio_source_agent"
down_revision = "0027_global_human_memory"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "studio_source_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("files_json", sa.Text(), nullable=False),
        sa.Column("provenance_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("change_summary", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("parent_source_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["studio_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "source_version", name="uq_studio_source_version"),
    )
    op.create_index("ix_studio_source_versions_tenant_id", "studio_source_versions", ["tenant_id"], unique=False)
    op.create_index("ix_studio_source_versions_project_id", "studio_source_versions", ["project_id"], unique=False)


def downgrade():
    op.drop_index("ix_studio_source_versions_project_id", table_name="studio_source_versions")
    op.drop_index("ix_studio_source_versions_tenant_id", table_name="studio_source_versions")
    op.drop_table("studio_source_versions")
