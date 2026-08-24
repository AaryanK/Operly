"""make action, approval and event ownership scope-aware

Revision ID: 0039_scope_owned_actions
Revises: 0038_solution_generation_leases
"""

from alembic import op
import sqlalchemy as sa

revision = "0039_scope_owned_actions"
down_revision = "0038_solution_generation_leases"
branch_labels = None
depends_on = None


_TABLES = (
    ("business_actions", "ck_business_actions_scope_owner", "ix_business_actions_owner_status"),
    ("approvals", "ck_approvals_scope_owner", "ix_approvals_owner_status"),
    ("business_events", "ck_business_events_scope_owner", "ix_business_events_owner_type_time"),
)


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table) if index.get("name")}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table, constraint_name, _ in _TABLES:
        if not inspector.has_table(table):
            continue
        columns = _columns(inspector, table)
        with op.batch_alter_table(table) as batch:
            if "scope_kind" not in columns:
                batch.add_column(
                    sa.Column(
                        "scope_kind",
                        sa.String(length=20),
                        nullable=False,
                        server_default="workspace",
                    )
                )
            if "owner_user_id" not in columns:
                batch.add_column(sa.Column("owner_user_id", sa.String(length=36), nullable=True))
                batch.create_foreign_key(
                    f"fk_{table}_owner_user_id_app_users",
                    "app_users",
                    ["owner_user_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
            if columns.get("tenant_id") is not None:
                batch.alter_column(
                    "tenant_id",
                    existing_type=sa.String(length=36),
                    nullable=True,
                )

        # Existing rows already have tenant_id and therefore remain Workspace-owned.
        bind.execute(sa.text(f"UPDATE {table} SET scope_kind='workspace' WHERE scope_kind IS NULL OR scope_kind=''"))

        # Re-inspect after batch recreation before adding the invariant.
        inspector = sa.inspect(bind)
        checks = {item.get("name") for item in inspector.get_check_constraints(table)}
        if constraint_name not in checks:
            with op.batch_alter_table(table) as batch:
                batch.create_check_constraint(
                    constraint_name,
                    "(scope_kind = 'workspace' AND tenant_id IS NOT NULL AND owner_user_id IS NULL) OR "
                    "(scope_kind = 'personal' AND tenant_id IS NULL AND owner_user_id IS NOT NULL)",
                )
        inspector = sa.inspect(bind)

    inspector = sa.inspect(bind)
    if inspector.has_table("business_actions"):
        indexes = _indexes(inspector, "business_actions")
        if "ix_business_actions_scope_kind" not in indexes:
            op.create_index("ix_business_actions_scope_kind", "business_actions", ["scope_kind"])
        if "ix_business_actions_owner_user_id" not in indexes:
            op.create_index("ix_business_actions_owner_user_id", "business_actions", ["owner_user_id"])
        if "ix_business_actions_owner_status" not in indexes:
            op.create_index(
                "ix_business_actions_owner_status",
                "business_actions",
                ["owner_user_id", "status", "created_at"],
            )

    inspector = sa.inspect(bind)
    if inspector.has_table("approvals"):
        indexes = _indexes(inspector, "approvals")
        if "ix_approvals_scope_kind" not in indexes:
            op.create_index("ix_approvals_scope_kind", "approvals", ["scope_kind"])
        if "ix_approvals_owner_user_id" not in indexes:
            op.create_index("ix_approvals_owner_user_id", "approvals", ["owner_user_id"])
        if "ix_approvals_owner_status" not in indexes:
            op.create_index(
                "ix_approvals_owner_status",
                "approvals",
                ["owner_user_id", "status", "created_at"],
            )

    inspector = sa.inspect(bind)
    if inspector.has_table("business_events"):
        indexes = _indexes(inspector, "business_events")
        if "ix_business_events_scope_kind" not in indexes:
            op.create_index("ix_business_events_scope_kind", "business_events", ["scope_kind"])
        if "ix_business_events_owner_user_id" not in indexes:
            op.create_index("ix_business_events_owner_user_id", "business_events", ["owner_user_id"])
        if "ix_business_events_owner_type_time" not in indexes:
            op.create_index(
                "ix_business_events_owner_type_time",
                "business_events",
                ["owner_user_id", "event_type", "occurred_at"],
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # The old schema cannot represent Personal-owned rows. Removing them is safer
    # than silently reassociating them with an unrelated workspace during downgrade.
    for table, _, _ in _TABLES:
        if inspector.has_table(table) and "scope_kind" in _columns(inspector, table):
            bind.execute(sa.text(f"DELETE FROM {table} WHERE scope_kind='personal'"))

    for table, constraint_name, composite_index in reversed(_TABLES):
        inspector = sa.inspect(bind)
        if not inspector.has_table(table):
            continue
        indexes = _indexes(inspector, table)
        for name in (
            composite_index,
            f"ix_{table}_owner_user_id",
            f"ix_{table}_scope_kind",
        ):
            if name in indexes:
                op.drop_index(name, table_name=table)

        inspector = sa.inspect(bind)
        checks = {item.get("name") for item in inspector.get_check_constraints(table)}
        with op.batch_alter_table(table) as batch:
            if constraint_name in checks:
                batch.drop_constraint(constraint_name, type_="check")
            columns = _columns(sa.inspect(bind), table)
            if "owner_user_id" in columns:
                batch.drop_constraint(
                    f"fk_{table}_owner_user_id_app_users",
                    type_="foreignkey",
                )
                batch.drop_column("owner_user_id")
            if "scope_kind" in columns:
                batch.drop_column("scope_kind")
            batch.alter_column(
                "tenant_id",
                existing_type=sa.String(length=36),
                nullable=False,
            )
