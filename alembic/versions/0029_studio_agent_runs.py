"""add durable Studio agent runs and visible progress events

Revision ID: 0029_studio_agent_runs
Revises: 0028_studio_source_agent
"""

from alembic import op
import sqlalchemy as sa

revision = "0029_studio_agent_runs"
down_revision = "0028_studio_source_agent"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # The historical baseline can materialize currently registered models on a
    # brand-new database, so every table creation is intentionally idempotent.
    if not inspector.has_table("studio_agent_runs"):
        op.create_table(
            "studio_agent_runs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("operation", sa.String(length=30), nullable=False),
            sa.Column("instruction", sa.Text(), nullable=False, server_default=""),
            sa.Column("context_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("state", sa.String(length=30), nullable=False, server_default="queued"),
            sa.Column("model_id", sa.String(length=200), nullable=True),
            sa.Column("source_id", sa.String(length=36), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_by", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["studio_projects.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_studio_agent_runs_tenant_id", "studio_agent_runs", ["tenant_id"], unique=False)
        op.create_index("ix_studio_agent_runs_project_id", "studio_agent_runs", ["project_id"], unique=False)
        op.create_index("ix_studio_agent_runs_state", "studio_agent_runs", ["state"], unique=False)

    inspector = sa.inspect(bind)
    if not inspector.has_table("studio_agent_events"):
        op.create_table(
            "studio_agent_events",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("phase", sa.String(length=40), nullable=False),
            sa.Column("summary", sa.String(length=1000), nullable=False),
            sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.ForeignKeyConstraint(["run_id"], ["studio_agent_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", "sequence", name="uq_studio_agent_event_sequence"),
        )
        op.create_index("ix_studio_agent_events_tenant_id", "studio_agent_events", ["tenant_id"], unique=False)
        op.create_index("ix_studio_agent_events_run_id", "studio_agent_events", ["run_id"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("studio_agent_events"):
        op.drop_index("ix_studio_agent_events_run_id", table_name="studio_agent_events")
        op.drop_index("ix_studio_agent_events_tenant_id", table_name="studio_agent_events")
        op.drop_table("studio_agent_events")
    inspector = sa.inspect(bind)
    if inspector.has_table("studio_agent_runs"):
        op.drop_index("ix_studio_agent_runs_state", table_name="studio_agent_runs")
        op.drop_index("ix_studio_agent_runs_project_id", table_name="studio_agent_runs")
        op.drop_index("ix_studio_agent_runs_tenant_id", table_name="studio_agent_runs")
        op.drop_table("studio_agent_runs")
