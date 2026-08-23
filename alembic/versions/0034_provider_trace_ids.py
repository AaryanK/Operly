"""widen provider causation trace identifiers

Revision ID: 0034_provider_trace_ids
Revises: 0033_personal_connectors
"""

from alembic import op
import sqlalchemy as sa

revision = "0034_provider_trace_ids"
down_revision = "0033_personal_connectors"
branch_labels = None
depends_on = None


def _widen(table_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return
    columns = {column["name"]: column for column in inspector.get_columns(table_name)}
    if "causation_id" not in columns:
        return
    with op.batch_alter_table(table_name) as batch:
        batch.alter_column(
            "causation_id",
            existing_type=sa.String(length=36),
            type_=sa.String(length=160),
            existing_nullable=True,
        )


def _narrow(table_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return
    columns = {column["name"]: column for column in inspector.get_columns(table_name)}
    if "causation_id" not in columns:
        return
    # Downgrade is intentionally allowed only for data that fits the historical
    # UUID-sized column. Deployments containing provider IDs longer than 36 chars
    # must clean/retain that provenance instead of silently truncating it.
    with op.batch_alter_table(table_name) as batch:
        batch.alter_column(
            "causation_id",
            existing_type=sa.String(length=160),
            type_=sa.String(length=36),
            existing_nullable=True,
        )


def upgrade():
    _widen("business_events")
    _widen("business_actions")


def downgrade():
    _narrow("business_actions")
    _narrow("business_events")
