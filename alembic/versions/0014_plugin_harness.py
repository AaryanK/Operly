"""plugin harness action identity

Revision ID: 0014_plugin_harness
Revises: 0013_company_operating_system
"""
from alembic import op
import sqlalchemy as sa

revision="0014_plugin_harness";down_revision="0013_company_operating_system";branch_labels=None;depends_on=None

def upgrade():
    columns={x["name"] for x in sa.inspect(op.get_bind()).get_columns("business_actions")}
    with op.batch_alter_table("business_actions") as batch:
        if "causation_id" not in columns: batch.add_column(sa.Column("causation_id",sa.String(36),nullable=True))
        if "idempotency_key" not in columns: batch.add_column(sa.Column("idempotency_key",sa.String(160),nullable=True))
    indexes={x["name"] for x in sa.inspect(op.get_bind()).get_indexes("business_actions")}
    if "ix_business_actions_causation_id" not in indexes: op.create_index("ix_business_actions_causation_id","business_actions",["causation_id"])
    if "ix_business_actions_idempotency_key" not in indexes: op.create_index("ix_business_actions_idempotency_key","business_actions",["idempotency_key"])

def downgrade():
    with op.batch_alter_table("business_actions") as batch:
        batch.drop_index("ix_business_actions_idempotency_key");batch.drop_index("ix_business_actions_causation_id")
        batch.drop_column("idempotency_key");batch.drop_column("causation_id")
