"""explicit platform job idempotency scope

Revision ID: 0055_platform_job_idempotency_scope
Revises: 0054_digital_runtime_support
"""

from alembic import op
import sqlalchemy as sa

revision = "0055_platform_job_idempotency_scope"
down_revision = "0054_digital_runtime_support"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("digital_platform_jobs"):
        return
    columns = {column["name"] for column in inspector.get_columns("digital_platform_jobs")}
    with op.batch_alter_table("digital_platform_jobs") as batch:
        if "idempotency_scope" not in columns:
            batch.add_column(
                sa.Column(
                    "idempotency_scope",
                    sa.String(80),
                    nullable=False,
                    server_default="platform",
                )
            )
        unique_names = {
            item.get("name") for item in inspector.get_unique_constraints("digital_platform_jobs")
        }
        if "uq_digital_platform_job_idempotency" in unique_names:
            batch.drop_constraint("uq_digital_platform_job_idempotency", type_="unique")
        batch.create_unique_constraint(
            "uq_digital_platform_job_idempotency",
            ["idempotency_scope", "idempotency_key"],
        )
        index_names = {item.get("name") for item in inspector.get_indexes("digital_platform_jobs")}
        if "ix_digital_platform_jobs_idempotency_scope" not in index_names:
            batch.create_index(
                "ix_digital_platform_jobs_idempotency_scope",
                ["idempotency_scope"],
            )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("digital_platform_jobs"):
        return
    columns = {column["name"] for column in inspector.get_columns("digital_platform_jobs")}
    if "idempotency_scope" not in columns:
        return
    with op.batch_alter_table("digital_platform_jobs") as batch:
        batch.drop_constraint("uq_digital_platform_job_idempotency", type_="unique")
        batch.create_unique_constraint(
            "uq_digital_platform_job_idempotency",
            ["tenant_id", "idempotency_key"],
        )
        batch.drop_index("ix_digital_platform_jobs_idempotency_scope")
        batch.drop_column("idempotency_scope")
