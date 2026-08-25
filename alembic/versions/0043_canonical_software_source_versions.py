"""add canonical immutable SoftwareProject source versions

Revision ID: 0043_canonical_software_source_versions
Revises: 0042_agent_computer_sessions
"""

from alembic import op
import sqlalchemy as sa

revision = "0043_canonical_software_source_versions"
down_revision = "0042_agent_computer_sessions"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("software_source_versions"):
        op.create_table(
            "software_source_versions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("source_version", sa.Integer(), nullable=False),
            sa.Column("parent_source_id", sa.String(length=36), nullable=True),
            sa.Column("runtime_profile", sa.String(length=160), nullable=False),
            sa.Column("bundle_digest", sa.String(length=80), nullable=False),
            sa.Column("manifest_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("files_json", sa.Text(), nullable=False),
            sa.Column("provenance_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("change_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("originating_run_id", sa.String(length=160), nullable=True),
            sa.Column("legacy_source_type", sa.String(length=40), nullable=True),
            sa.Column("legacy_source_reference", sa.String(length=120), nullable=True),
            sa.Column("created_by", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["parent_source_id"], ["software_source_versions.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["software_projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "source_version", name="uq_software_source_project_version"),
            sa.UniqueConstraint(
                "tenant_id",
                "legacy_source_type",
                "legacy_source_reference",
                name="uq_software_source_legacy_reference",
            ),
        )
        op.create_index("ix_software_source_versions_tenant_id", "software_source_versions", ["tenant_id"])
        op.create_index("ix_software_source_versions_project_id", "software_source_versions", ["project_id"])
        op.create_index("ix_software_source_versions_parent_source_id", "software_source_versions", ["parent_source_id"])
        op.create_index("ix_software_source_versions_bundle_digest", "software_source_versions", ["bundle_digest"])
        op.create_index("ix_software_source_versions_originating_run_id", "software_source_versions", ["originating_run_id"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("software_source_versions"):
        op.drop_table("software_source_versions")
