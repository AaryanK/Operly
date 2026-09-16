"""durable agent task checkpoint metadata

Revision ID: 0060_durable_agent_task_checkpoints
Revises: 0059_agent_spend_controls
"""

from alembic import op
import sqlalchemy as sa

revision = "0060_durable_agent_task_checkpoints"
down_revision = "0059_agent_spend_controls"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("agent_runtime_runs"):
        return

    existing = _columns(inspector, "agent_runtime_runs")
    additions = (
        ("plan_version", sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1")),
        ("checkpoint_version", sa.Column("checkpoint_version", sa.Integer(), nullable=False, server_default="0")),
        (
            "grants_reference_json",
            sa.Column("grants_reference_json", sa.Text(), nullable=False, server_default="{}"),
        ),
        (
            "verified_observations_json",
            sa.Column("verified_observations_json", sa.Text(), nullable=False, server_default="[]"),
        ),
        (
            "wait_predicate_json",
            sa.Column("wait_predicate_json", sa.Text(), nullable=False, server_default="{}"),
        ),
        ("deadline_at", sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True)),
    )
    for name, column in additions:
        if name not in existing:
            op.add_column("agent_runtime_runs", column)

    inspector = sa.inspect(bind)
    index_names = {index["name"] for index in inspector.get_indexes("agent_runtime_runs")}
    if "ix_agent_runtime_runs_deadline_at" not in index_names:
        op.create_index(
            "ix_agent_runtime_runs_deadline_at",
            "agent_runtime_runs",
            ["deadline_at"],
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("agent_runtime_runs"):
        return

    index_names = {index["name"] for index in inspector.get_indexes("agent_runtime_runs")}
    if "ix_agent_runtime_runs_deadline_at" in index_names:
        op.drop_index("ix_agent_runtime_runs_deadline_at", table_name="agent_runtime_runs")

    existing = _columns(sa.inspect(bind), "agent_runtime_runs")
    for name in (
        "deadline_at",
        "wait_predicate_json",
        "verified_observations_json",
        "grants_reference_json",
        "checkpoint_version",
        "plan_version",
    ):
        if name in existing:
            op.drop_column("agent_runtime_runs", name)
