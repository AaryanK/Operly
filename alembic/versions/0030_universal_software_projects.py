"""add canonical software projects and service bindings

Revision ID: 0030_universal_software_projects
Revises: 0029_studio_agent_runs
"""

from alembic import op
import sqlalchemy as sa

revision = "0030_universal_software_projects"
down_revision = "0029_studio_agent_runs"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("software_projects"):
        op.create_table(
            "software_projects",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("state", sa.String(length=30), nullable=False, server_default="draft"),
            sa.Column("active_source_version_id", sa.String(length=120), nullable=True),
            sa.Column("active_runtime_id", sa.String(length=160), nullable=True),
            sa.Column("legacy_runtime_type", sa.String(length=40), nullable=True),
            sa.Column("legacy_runtime_reference", sa.String(length=120), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_by", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id",
                "legacy_runtime_type",
                "legacy_runtime_reference",
                name="uq_software_project_legacy_runtime",
            ),
        )
        op.create_index("ix_software_projects_tenant_id", "software_projects", ["tenant_id"], unique=False)
        op.create_index("ix_software_projects_state", "software_projects", ["state"], unique=False)

    inspector = sa.inspect(bind)
    if not inspector.has_table("service_bindings"):
        op.create_table(
            "service_bindings",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("semantic_name", sa.String(length=160), nullable=False),
            sa.Column("capability_id", sa.String(length=200), nullable=False),
            sa.Column("capability_version", sa.String(length=40), nullable=False, server_default="1.0.0"),
            sa.Column("binding_mode", sa.String(length=40), nullable=False, server_default="capability_gateway"),
            sa.Column("principal_scope", sa.String(length=80), nullable=False, server_default="project_runtime"),
            sa.Column("configuration_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
            sa.Column("created_by", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["software_projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "project_id",
                "semantic_name",
                name="uq_service_binding_project_semantic_name",
            ),
        )
        op.create_index("ix_service_bindings_tenant_id", "service_bindings", ["tenant_id"], unique=False)
        op.create_index("ix_service_bindings_project_id", "service_bindings", ["project_id"], unique=False)
        op.create_index("ix_service_bindings_capability_id", "service_bindings", ["capability_id"], unique=False)
        op.create_index("ix_service_bindings_status", "service_bindings", ["status"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("service_bindings"):
        op.drop_index("ix_service_bindings_status", table_name="service_bindings")
        op.drop_index("ix_service_bindings_capability_id", table_name="service_bindings")
        op.drop_index("ix_service_bindings_project_id", table_name="service_bindings")
        op.drop_index("ix_service_bindings_tenant_id", table_name="service_bindings")
        op.drop_table("service_bindings")

    inspector = sa.inspect(bind)
    if inspector.has_table("software_projects"):
        op.drop_index("ix_software_projects_state", table_name="software_projects")
        op.drop_index("ix_software_projects_tenant_id", table_name="software_projects")
        op.drop_table("software_projects")
