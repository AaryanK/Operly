"""add run-scoped Agent Computer session handles

Revision ID: 0042_agent_computer_sessions
Revises: 0041_agent_artifacts
"""

from alembic import op
import sqlalchemy as sa

revision = "0042_agent_computer_sessions"
down_revision = "0041_agent_artifacts"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {str(item["name"]) for item in inspector.get_columns(table)}


def upgrade():
    columns = _columns("agent_runs")
    if not columns:
        return
    with op.batch_alter_table("agent_runs") as batch:
        if "computer_session_id" not in columns:
            batch.add_column(sa.Column("computer_session_id", sa.String(length=160), nullable=True))
        if "computer_session_updated_at" not in columns:
            batch.add_column(sa.Column("computer_session_updated_at", sa.DateTime(), nullable=True))
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("agent_runs")}
    if "ix_agent_runs_computer_session_id" not in indexes:
        op.create_index("ix_agent_runs_computer_session_id", "agent_runs", ["computer_session_id"])


def downgrade():
    columns = _columns("agent_runs")
    if not columns:
        return
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("agent_runs")}
    if "ix_agent_runs_computer_session_id" in indexes:
        op.drop_index("ix_agent_runs_computer_session_id", table_name="agent_runs")
    with op.batch_alter_table("agent_runs") as batch:
        if "computer_session_updated_at" in columns:
            batch.drop_column("computer_session_updated_at")
        if "computer_session_id" in columns:
            batch.drop_column("computer_session_id")
