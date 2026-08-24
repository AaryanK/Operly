"""add durable leases and ownership to Solution generation jobs

Revision ID: 0038_solution_generation_leases
Revises: 0037_harness_native_tasks
"""

from alembic import op
import sqlalchemy as sa

revision = "0038_solution_generation_leases"
down_revision = "0037_harness_native_tasks"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table) if index.get("name")}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("solution_jobs"):
        return

    columns = _columns(inspector, "solution_jobs")
    with op.batch_alter_table("solution_jobs") as batch:
        if "created_by" not in columns:
            batch.add_column(sa.Column("created_by", sa.String(length=36), nullable=True))
            batch.create_foreign_key(
                "fk_solution_jobs_created_by_app_users",
                "app_users",
                ["created_by"],
                ["id"],
            )
        if "plan_id" not in columns:
            batch.add_column(sa.Column("plan_id", sa.String(length=36), nullable=True))
            batch.create_foreign_key(
                "fk_solution_jobs_plan_id_software_plans",
                "software_plans",
                ["plan_id"],
                ["id"],
            )
        if "locked_by" not in columns:
            batch.add_column(sa.Column("locked_by", sa.String(length=160), nullable=True))
        if "lease_expires_at" not in columns:
            batch.add_column(sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
        if "heartbeat_at" not in columns:
            batch.add_column(sa.Column("heartbeat_at", sa.DateTime(), nullable=True))

    inspector = sa.inspect(bind)
    indexes = _indexes(inspector, "solution_jobs")
    for name, columns in (
        ("ix_solution_jobs_created_by", ["created_by"]),
        ("ix_solution_jobs_plan_id", ["plan_id"]),
        ("ix_solution_jobs_locked_by", ["locked_by"]),
        ("ix_solution_jobs_lease_expires_at", ["lease_expires_at"]),
    ):
        if name not in indexes:
            op.create_index(name, "solution_jobs", columns, unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("solution_jobs"):
        return

    indexes = _indexes(inspector, "solution_jobs")
    for name in (
        "ix_solution_jobs_lease_expires_at",
        "ix_solution_jobs_locked_by",
        "ix_solution_jobs_plan_id",
        "ix_solution_jobs_created_by",
    ):
        if name in indexes:
            op.drop_index(name, table_name="solution_jobs")

    columns = _columns(sa.inspect(bind), "solution_jobs")
    with op.batch_alter_table("solution_jobs") as batch:
        if "plan_id" in columns:
            batch.drop_constraint("fk_solution_jobs_plan_id_software_plans", type_="foreignkey")
            batch.drop_column("plan_id")
        if "created_by" in columns:
            batch.drop_constraint("fk_solution_jobs_created_by_app_users", type_="foreignkey")
            batch.drop_column("created_by")
        if "heartbeat_at" in columns:
            batch.drop_column("heartbeat_at")
        if "lease_expires_at" in columns:
            batch.drop_column("lease_expires_at")
        if "locked_by" in columns:
            batch.drop_column("locked_by")
