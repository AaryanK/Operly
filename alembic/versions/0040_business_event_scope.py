"""make business events explicitly scope-owned

Revision ID: 0040_business_event_scope
Revises: 0039_scope_aware_actions
"""

from alembic import op
import sqlalchemy as sa

revision = "0040_business_event_scope"
down_revision = "0039_scope_aware_actions"
branch_labels = None
depends_on = None


def _columns(inspector) -> dict[str, dict]:
    return {column["name"]: column for column in inspector.get_columns("business_events")}


def _indexes(inspector) -> set[str]:
    return {
        index["name"]
        for index in inspector.get_indexes("business_events")
        if index.get("name")
    }


def _checks(inspector) -> set[str]:
    return {
        constraint["name"]
        for constraint in inspector.get_check_constraints("business_events")
        if constraint.get("name")
    }


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("business_events"):
        return

    columns = _columns(inspector)
    checks = _checks(inspector)
    with op.batch_alter_table("business_events") as batch:
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
                "fk_business_events_owner_user_id_app_users",
                "app_users",
                ["owner_user_id"],
                ["id"],
                ondelete="CASCADE",
            )
        if "ck_business_events_scope_owner" not in checks:
            batch.create_check_constraint(
                "ck_business_events_scope_owner",
                "(scope_kind = 'workspace' AND tenant_id IS NOT NULL AND owner_user_id IS NULL) "
                "OR (scope_kind = 'personal' AND tenant_id IS NULL AND owner_user_id IS NOT NULL)",
            )

    # Existing events were tenant-owned before this migration and remain Workspace events.
    bind.execute(
        sa.text(
            "UPDATE business_events SET scope_kind='workspace' "
            "WHERE scope_kind IS NULL OR scope_kind=''"
        )
    )

    inspector = sa.inspect(bind)
    indexes = _indexes(inspector)
    if "ix_business_events_scope_kind" not in indexes:
        op.create_index(
            "ix_business_events_scope_kind",
            "business_events",
            ["scope_kind"],
            unique=False,
        )
    if "ix_business_events_owner_user_id" not in indexes:
        op.create_index(
            "ix_business_events_owner_user_id",
            "business_events",
            ["owner_user_id"],
            unique=False,
        )
    if "ix_business_events_owner_type_time" not in indexes:
        op.create_index(
            "ix_business_events_owner_type_time",
            "business_events",
            ["owner_user_id", "event_type", "occurred_at"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("business_events"):
        return

    columns = _columns(inspector)
    if "scope_kind" in columns:
        personal_count = bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM business_events WHERE scope_kind = 'personal'"
            )
        ).scalar_one()
        if personal_count:
            raise RuntimeError(
                "Cannot downgrade business_events while Personal-scoped rows exist"
            )

    indexes = _indexes(sa.inspect(bind))
    for name in (
        "ix_business_events_owner_type_time",
        "ix_business_events_owner_user_id",
        "ix_business_events_scope_kind",
    ):
        if name in indexes:
            op.drop_index(name, table_name="business_events")

    inspector = sa.inspect(bind)
    checks = _checks(inspector)
    columns = _columns(inspector)
    with op.batch_alter_table("business_events") as batch:
        if "ck_business_events_scope_owner" in checks:
            batch.drop_constraint("ck_business_events_scope_owner", type_="check")
        if "owner_user_id" in columns:
            batch.drop_constraint(
                "fk_business_events_owner_user_id_app_users",
                type_="foreignkey",
            )
            batch.drop_column("owner_user_id")
        if "scope_kind" in columns:
            batch.drop_column("scope_kind")
        if columns.get("tenant_id", {}).get("nullable") is True:
            batch.alter_column(
                "tenant_id",
                existing_type=sa.String(length=36),
                nullable=False,
            )
