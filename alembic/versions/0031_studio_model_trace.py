"""add owner-only Studio model trace rows

Revision ID: 0031_studio_model_trace
Revises: 0030_universal_software_projects
"""

from alembic import op
import sqlalchemy as sa

revision = "0031_studio_model_trace"
down_revision = "0030_universal_software_projects"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("studio_model_traces"):
        return
    op.create_table(
        "studio_model_traces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("call_index", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=30), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["studio_agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "call_index", "phase", name="uq_studio_model_trace_call_phase"),
    )
    op.create_index("ix_studio_model_traces_tenant_id", "studio_model_traces", ["tenant_id"], unique=False)
    op.create_index("ix_studio_model_traces_run_id", "studio_model_traces", ["run_id"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("studio_model_traces"):
        return
    op.drop_index("ix_studio_model_traces_run_id", table_name="studio_model_traces")
    op.drop_index("ix_studio_model_traces_tenant_id", table_name="studio_model_traces")
    op.drop_table("studio_model_traces")
