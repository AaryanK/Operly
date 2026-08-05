"""Add managed-record runtime compatibility and idempotency.

Revision ID: 0004_managed_record_runtime
Revises: 0003_application_builder_core
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_managed_record_runtime"
down_revision = "0003_application_builder_core"
branch_labels = None
depends_on = None


def upgrade():
    columns={item["name"] for item in sa.inspect(op.get_bind()).get_columns("managed_records")}
    if {"application_version_id","idempotency_key"}<=columns:
        return
    op.add_column("managed_records", sa.Column("application_version_id", sa.String(36), nullable=True))
    op.add_column("managed_records", sa.Column("idempotency_key", sa.String(120), nullable=True))
    op.execute("UPDATE managed_records SET application_version_id = (SELECT active_version_id FROM managed_applications WHERE managed_applications.id = managed_records.application_id)")
    op.execute("UPDATE managed_records SET idempotency_key = 'legacy-' || id")
    with op.batch_alter_table("managed_records") as batch:
        batch.create_foreign_key("fk_managed_records_application_version", "application_versions", ["application_version_id"], ["id"])
        batch.alter_column("application_version_id", nullable=False)
        batch.alter_column("idempotency_key", nullable=False)
        batch.create_unique_constraint("uq_managed_record_idempotency", ["tenant_id", "application_id", "idempotency_key"])
    op.create_index("ix_managed_records_application_version_id", "managed_records", ["application_version_id"])


def downgrade():
    raise RuntimeError("Managed record runtime downgrade is unsafe; restore a verified backup")
