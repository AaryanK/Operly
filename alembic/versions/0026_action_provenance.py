"""action principal/client/origin provenance

Revision ID: 0026_action_provenance
Revises: 0025_principal_conversations
"""

from alembic import op
import sqlalchemy as sa

revision = "0026_action_provenance"
down_revision = "0025_principal_conversations"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("business_actions")}
    additions = {
        "principal_id": sa.Column("principal_id", sa.String(120), nullable=True),
        "client_id": sa.Column("client_id", sa.String(120), nullable=True),
        "origin": sa.Column("origin", sa.String(40), nullable=True),
        "connector_id": sa.Column("connector_id", sa.String(120), nullable=True),
        "resource_type": sa.Column("resource_type", sa.String(80), nullable=True),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("business_actions", column)
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("business_actions")}
    for name in ("principal_id", "client_id", "origin", "connector_id"):
        index_name = f"ix_business_actions_{name}"
        if index_name not in indexes:
            op.create_index(index_name, "business_actions", [name])


def downgrade():
    for name in ("connector_id", "origin", "client_id", "principal_id"):
        op.drop_index(f"ix_business_actions_{name}", table_name="business_actions")
    for name in ("resource_type", "connector_id", "origin", "client_id", "principal_id"):
        op.drop_column("business_actions", name)
