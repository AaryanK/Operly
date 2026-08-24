"""make durable tasks harness-native and personal-scope capable

Revision ID: 0037_harness_native_tasks
Revises: 0036_workspace_presentation
"""

from alembic import op
import sqlalchemy as sa

revision = "0037_harness_native_tasks"
down_revision = "0036_workspace_presentation"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table) if index.get("name")}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("tasks"):
        columns = _columns(inspector, "tasks")
        with op.batch_alter_table("tasks") as batch:
            batch.alter_column(
                "tenant_id",
                existing_type=sa.String(length=36),
                nullable=True,
            )
            if "owner_user_id" not in columns:
                batch.add_column(sa.Column("owner_user_id", sa.String(length=36), nullable=True))
                batch.create_foreign_key(
                    "fk_tasks_owner_user_id_app_users",
                    "app_users",
                    ["owner_user_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
        inspector = sa.inspect(bind)
        indexes = _indexes(inspector, "tasks")
        if "ix_tasks_owner_user_id" not in indexes:
            op.create_index("ix_tasks_owner_user_id", "tasks", ["owner_user_id"], unique=False)

    inspector = sa.inspect(bind)
    if inspector.has_table("scheduled_jobs"):
        columns = _columns(inspector, "scheduled_jobs")
        with op.batch_alter_table("scheduled_jobs") as batch:
            batch.alter_column(
                "tenant_id",
                existing_type=sa.String(length=36),
                nullable=True,
            )
            if "task_id" not in columns:
                batch.add_column(sa.Column("task_id", sa.String(length=36), nullable=True))
                batch.create_foreign_key(
                    "fk_scheduled_jobs_task_id_tasks",
                    "tasks",
                    ["task_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
        inspector = sa.inspect(bind)
        indexes = _indexes(inspector, "scheduled_jobs")
        if "ix_scheduled_jobs_task_id" not in indexes:
            op.create_index("ix_scheduled_jobs_task_id", "scheduled_jobs", ["task_id"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("scheduled_jobs"):
        columns = _columns(inspector, "scheduled_jobs")
        indexes = _indexes(inspector, "scheduled_jobs")
        if "ix_scheduled_jobs_task_id" in indexes:
            op.drop_index("ix_scheduled_jobs_task_id", table_name="scheduled_jobs")
        with op.batch_alter_table("scheduled_jobs") as batch:
            if "task_id" in columns:
                batch.drop_constraint("fk_scheduled_jobs_task_id_tasks", type_="foreignkey")
                batch.drop_column("task_id")
            batch.alter_column(
                "tenant_id",
                existing_type=sa.String(length=36),
                nullable=False,
            )

    inspector = sa.inspect(bind)
    if inspector.has_table("tasks"):
        columns = _columns(inspector, "tasks")
        indexes = _indexes(inspector, "tasks")
        if "ix_tasks_owner_user_id" in indexes:
            op.drop_index("ix_tasks_owner_user_id", table_name="tasks")
        with op.batch_alter_table("tasks") as batch:
            if "owner_user_id" in columns:
                batch.drop_constraint("fk_tasks_owner_user_id_app_users", type_="foreignkey")
                batch.drop_column("owner_user_id")
            batch.alter_column(
                "tenant_id",
                existing_type=sa.String(length=36),
                nullable=False,
            )
