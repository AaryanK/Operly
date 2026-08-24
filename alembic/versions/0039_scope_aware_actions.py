"""make actions and approvals explicitly scope-owned

Revision ID: 0039_scope_aware_actions
Revises: 0038_solution_generation_leases
"""

from alembic import op
import sqlalchemy as sa

revision = "0039_scope_aware_actions"
down_revision = "0038_solution_generation_leases"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> dict[str, dict]:
    return {column["name"]: column for column in inspector.get_columns(table)}


def _indexes(inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table) if index.get("name")}


def _checks(inspector, table: str) -> set[str]:
    return {
        constraint["name"]
        for constraint in inspector.get_check_constraints(table)
        if constraint.get("name")
    }


def _upgrade_scope_table(
    table: str,
    *,
    owner_fk_name: str,
    check_name: str,
):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        return

    columns = _columns(inspector, table)
    checks = _checks(inspector, table)
    with op.batch_alter_table(table) as batch:
        if columns.get("tenant_id", {}).get("nullable") is False:
            batch.alter_column(
                "tenant_id",
                existing_type=sa.String(length=36),
                nullable=True,
            )
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
                owner_fk_name,
                "app_users",
                ["owner_user_id"],
                ["id"],
                ondelete="CASCADE",
            )
        if check_name not in checks:
            batch.create_check_constraint(
                check_name,
                "(scope_kind = 'workspace' AND tenant_id IS NOT NULL AND owner_user_id IS NULL) "
                "OR (scope_kind = 'personal' AND tenant_id IS NULL AND owner_user_id IS NOT NULL)",
            )

    inspector = sa.inspect(bind)
    indexes = _indexes(inspector, table)
    for name, column in (
        (f"ix_{table}_scope_kind", "scope_kind"),
        (f"ix_{table}_owner_user_id", "owner_user_id"),
    ):
        if name not in indexes:
            op.create_index(name, table, [column], unique=False)


def upgrade():
    _upgrade_scope_table(
        "business_actions",
        owner_fk_name="fk_business_actions_owner_user_id_app_users",
        check_name="ck_business_actions_scope_owner",
    )
    _upgrade_scope_table(
        "approvals",
        owner_fk_name="fk_approvals_owner_user_id_app_users",
        check_name="ck_approvals_scope_owner",
    )


def _downgrade_scope_table(
    table: str,
    *,
    owner_fk_name: str,
    check_name: str,
):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        return

    columns = _columns(inspector, table)
    if "scope_kind" in columns:
        personal_count = bind.execute(
            sa.text(f"SELECT COUNT(*) FROM {table} WHERE scope_kind = 'personal'")
        ).scalar_one()
        if personal_count:
            raise RuntimeError(
                f"Cannot downgrade {table} while Personal-scoped rows exist"
            )

    indexes = _indexes(inspector, table)
    for name in (f"ix_{table}_owner_user_id", f"ix_{table}_scope_kind"):
        if name in indexes:
            op.drop_index(name, table_name=table)

    checks = _checks(sa.inspect(bind), table)
    columns = _columns(sa.inspect(bind), table)
    with op.batch_alter_table(table) as batch:
        if check_name in checks:
            batch.drop_constraint(check_name, type_="check")
        if "owner_user_id" in columns:
            batch.drop_constraint(owner_fk_name, type_="foreignkey")
            batch.drop_column("owner_user_id")
        if "scope_kind" in columns:
            batch.drop_column("scope_kind")
        if columns.get("tenant_id", {}).get("nullable") is True:
            batch.alter_column(
                "tenant_id",
                existing_type=sa.String(length=36),
                nullable=False,
            )


def downgrade():
    _downgrade_scope_table(
        "business_actions",
        owner_fk_name="fk_business_actions_owner_user_id_app_users",
        check_name="ck_business_actions_scope_owner",
    )
    _downgrade_scope_table(
        "approvals",
        owner_fk_name="fk_approvals_owner_user_id_app_users",
        check_name="ck_approvals_scope_owner",
    )
