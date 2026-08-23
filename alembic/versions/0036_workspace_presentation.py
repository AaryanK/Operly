"""add workspace presentation metadata

Revision ID: 0036_workspace_presentation
Revises: 0035_conversation_runtime_trace
"""

from alembic import op
import sqlalchemy as sa

revision = "0036_workspace_presentation"
down_revision = "0035_conversation_runtime_trace"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("tenants"):
        return
    columns = {column["name"] for column in inspector.get_columns("tenants")}
    if "logo_url" not in columns:
        op.add_column("tenants", sa.Column("logo_url", sa.String(length=1000), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("tenants"):
        return
    columns = {column["name"] for column in inspector.get_columns("tenants")}
    if "logo_url" in columns:
        op.drop_column("tenants", "logo_url")
