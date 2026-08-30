"""add Operly kernel v3 trace and event backbone

Revision ID: 0047_operly_kernel_v3
Revises: 0046_mature_workspace_suite
"""

from alembic import op
import sqlalchemy as sa

revision = "0047_operly_kernel_v3"
down_revision = "0046_mature_workspace_suite"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("kernel_runs"):
        op.create_table(
            "kernel_runs",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("scope_kind", sa.String(length=20), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=True),
            sa.Column("owner_user_id", sa.String(length=36), nullable=True),
            sa.Column("principal_id", sa.String(length=160), nullable=True),
            sa.Column("channel", sa.String(length=40), nullable=False),
            sa.Column("surface", sa.String(length=40), nullable=False),
            sa.Column("conversation_id", sa.String(length=160), nullable=True),
            sa.Column("goal", sa.Text(), nullable=False),
            sa.Column("capability_id", sa.String(length=160), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("result_json", sa.Text(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["workspace_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["owner_user_id"], ["app_users.id"], ondelete="CASCADE"),
        )
        for column in (
            "scope_kind",
            "workspace_id",
            "owner_user_id",
            "principal_id",
            "conversation_id",
            "capability_id",
            "status",
            "started_at",
        ):
            op.create_index(f"ix_kernel_runs_{column}", "kernel_runs", [column])

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("kernel_run_steps"):
        op.create_table(
            "kernel_run_steps",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("step_number", sa.Integer(), nullable=False),
            sa.Column("step_name", sa.String(length=80), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["kernel_runs.id"], ondelete="CASCADE"),
        )
        for column in ("run_id", "step_name", "created_at"):
            op.create_index(f"ix_kernel_run_steps_{column}", "kernel_run_steps", [column])

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("kernel_events"):
        op.create_table(
            "kernel_events",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("event_type", sa.String(length=160), nullable=False),
            sa.Column("scope_kind", sa.String(length=20), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=True),
            sa.Column("owner_user_id", sa.String(length=36), nullable=True),
            sa.Column("principal_id", sa.String(length=160), nullable=True),
            sa.Column("actor_type", sa.String(length=40), nullable=False),
            sa.Column("actor_id", sa.String(length=160), nullable=True),
            sa.Column("initiator_principal_id", sa.String(length=160), nullable=True),
            sa.Column("executor_principal_id", sa.String(length=160), nullable=True),
            sa.Column("capability_id", sa.String(length=160), nullable=True),
            sa.Column("resource_type", sa.String(length=100), nullable=True),
            sa.Column("resource_id", sa.String(length=160), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["owner_user_id"], ["app_users.id"], ondelete="CASCADE"),
        )
        for column in (
            "event_type",
            "scope_kind",
            "workspace_id",
            "owner_user_id",
            "principal_id",
            "capability_id",
            "resource_type",
            "resource_id",
            "created_at",
        ):
            op.create_index(f"ix_kernel_events_{column}", "kernel_events", [column])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    for table in ("kernel_events", "kernel_run_steps", "kernel_runs"):
        if inspector.has_table(table):
            op.drop_table(table)
