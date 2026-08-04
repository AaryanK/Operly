"""Create and normalize the Dashboard Studio schema.

Revision ID: 0002_dashboard_studio
Revises: 0001_operly_core
"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002_dashboard_studio"
down_revision = "0001_operly_core"
branch_labels = None
depends_on = None

TABLES = [
    "dashboard_customizations", "dashboard_change_sets",
    "dashboard_change_operations", "app_configuration_versions",
    "dashboard_studio_audits",
]
VALID_STATUSES = {"draft", "proposed", "previewing", "approved", "applying", "applied", "rejected", "rolled_back", "failed"}
NAMING = {
    "ix": "ix_%(column_0_label)s", "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
INDEXES = {
    "dashboard_customizations":[("ix_dashboard_customizations_tenant_id",["tenant_id"]),("ix_dashboard_customizations_screen_id",["screen_id"]),("ix_dashboard_customizations_component_id",["component_id"])],
    "dashboard_change_sets":[("ix_dashboard_change_sets_tenant_id",["tenant_id"]),("ix_dashboard_change_sets_screen_id",["screen_id"]),("ix_dashboard_change_sets_status",["status"])],
    "dashboard_change_operations":[("ix_dashboard_change_operations_tenant_id",["tenant_id"]),("ix_dashboard_change_operations_change_set_id",["change_set_id"])],
    "app_configuration_versions":[("ix_app_configuration_versions_tenant_id",["tenant_id"]),("ix_app_configuration_versions_active",["active"])],
    "dashboard_studio_audits":[("ix_dashboard_studio_audits_tenant_id",["tenant_id"])],
}


def _rows(bind, sql):
    return bind.execute(sa.text(sql)).mappings().all()


def _validate_and_normalize(bind, existing):
    if not existing:
        return
    required_core = {"tenants", "app_users"}
    if not required_core <= set(inspect(bind).get_table_names()):
        raise RuntimeError("Unsupported legacy schema: Dashboard Studio exists without tenants/app_users")

    for table in existing:
        columns = {c["name"] for c in inspect(bind).get_columns(table)}
        if "tenant_id" in columns and _rows(bind, f"SELECT id FROM {table} WHERE tenant_id IS NULL LIMIT 1"):
            raise RuntimeError(f"Unsafe legacy data: {table} contains a null workspace")
        if "tenant_id" in columns and _rows(bind, f"SELECT d.id FROM {table} d LEFT JOIN tenants t ON t.id=d.tenant_id WHERE t.id IS NULL LIMIT 1"):
            raise RuntimeError(f"Unsafe legacy data: {table} references an unknown workspace")
        for stamp in ("created_at", "updated_at"):
            if stamp in columns:
                result = bind.execute(sa.text(f"UPDATE {table} SET {stamp}=CURRENT_TIMESTAMP WHERE {stamp} IS NULL"))
                if result.rowcount:
                    print(f"normalized {result.rowcount} missing {table}.{stamp} values")

    if "dashboard_change_sets" in existing:
        bad = _rows(bind, "SELECT status, count(*) AS n FROM dashboard_change_sets GROUP BY status")
        unknown = [r["status"] for r in bad if r["status"] not in VALID_STATUSES]
        if unknown:
            raise RuntimeError("Unsafe legacy data: unknown Dashboard Studio ChangeSet status")
        for column in ("target_component_ids_json", "before_json", "after_json", "validation_json", "rollback_json"):
            if column in {c["name"] for c in inspect(bind).get_columns("dashboard_change_sets")}:
                for row in _rows(bind, f"SELECT {column} AS value FROM dashboard_change_sets"):
                    try: json.loads(row["value"])
                    except (TypeError, json.JSONDecodeError) as error: raise RuntimeError(f"Unsafe legacy data: invalid {column}") from error

    if "app_configuration_versions" in existing:
        duplicates = _rows(bind, "SELECT tenant_id,version_number FROM app_configuration_versions GROUP BY tenant_id,version_number HAVING count(*)>1")
        if duplicates:
            raise RuntimeError("Unsafe legacy data: duplicate workspace version numbers")
        if _rows(bind, "SELECT id FROM app_configuration_versions WHERE version_number IS NULL LIMIT 1"):
            raise RuntimeError("Unsafe legacy data: missing version number")
        for row in _rows(bind, "SELECT snapshot_json,affected_json FROM app_configuration_versions"):
            try: json.loads(row["snapshot_json"]); json.loads(row["affected_json"])
            except (TypeError, json.JSONDecodeError) as error: raise RuntimeError("Unsafe legacy data: invalid version JSON") from error
        tenants = _rows(bind, "SELECT tenant_id FROM app_configuration_versions WHERE active=1 GROUP BY tenant_id HAVING count(*)>1")
        for tenant in tenants:
            keep = bind.execute(sa.text("SELECT id FROM app_configuration_versions WHERE tenant_id=:tenant AND active=1 ORDER BY version_number DESC,created_at DESC LIMIT 1"), {"tenant":tenant["tenant_id"]}).scalar_one()
            result = bind.execute(sa.text("UPDATE app_configuration_versions SET active=0 WHERE tenant_id=:tenant AND active=1 AND id<>:keep"), {"tenant":tenant["tenant_id"],"keep":keep})
            print(f"normalized {result.rowcount} duplicate active configuration versions")

    if {"dashboard_change_operations", "dashboard_change_sets"} <= existing:
        if _rows(bind, "SELECT o.id FROM dashboard_change_operations o LEFT JOIN dashboard_change_sets c ON c.id=o.change_set_id WHERE c.id IS NULL LIMIT 1"):
            raise RuntimeError("Unsafe legacy data: orphaned ChangeSet operation")
        if _rows(bind, "SELECT change_set_id,position FROM dashboard_change_operations GROUP BY change_set_id,position HAVING count(*)>1 LIMIT 1"):
            raise RuntimeError("Unsafe legacy data: duplicate ChangeSet operation positions")

    if {"app_configuration_versions","dashboard_change_sets"} <= existing:
        if _rows(bind,"SELECT v.id FROM app_configuration_versions v LEFT JOIN dashboard_change_sets c ON c.id=v.originating_change_set_id WHERE v.originating_change_set_id IS NOT NULL AND c.id IS NULL LIMIT 1"):
            raise RuntimeError("Unsafe legacy data: version references an unknown originating ChangeSet")
        if _rows(bind,"SELECT c.id FROM dashboard_change_sets c LEFT JOIN app_configuration_versions v ON v.id=c.applied_version_id WHERE c.applied_version_id IS NOT NULL AND v.id IS NULL LIMIT 1"):
            raise RuntimeError("Unsafe legacy data: ChangeSet references an unknown applied version")
        version_columns={c["name"] for c in inspect(bind).get_columns("app_configuration_versions")}
        if "source_version_id" in version_columns and _rows(bind,"SELECT v.id FROM app_configuration_versions v LEFT JOIN app_configuration_versions s ON s.id=v.source_version_id WHERE v.source_version_id IS NOT NULL AND s.id IS NULL LIMIT 1"):
            raise RuntimeError("Unsafe legacy data: rollback references an unknown source version")

    actor_columns = [("dashboard_customizations","updated_by"),("dashboard_change_sets","created_by"),("app_configuration_versions","created_by"),("dashboard_studio_audits","actor_id")]
    for table,column in actor_columns:
        if table in existing and _rows(bind, f"SELECT d.id FROM {table} d LEFT JOIN app_users u ON u.id=d.{column} WHERE u.id IS NULL LIMIT 1"):
            raise RuntimeError(f"Unsafe legacy data: {table}.{column} does not reference an application user")


def _create_missing(bind, existing):
    from packages.database.db import Base
    from packages.database.schema import import_all_models
    import_all_models()
    for table in Base.metadata.sorted_tables:
        if table.name in TABLES and table.name not in existing:
            table.create(bind, checkfirst=True)


def _upgrade_existing(bind, original):
    recreate="always" if bind.dialect.name=="sqlite" else "auto"
    if "app_configuration_versions" in original:
        cols = {c["name"] for c in inspect(bind).get_columns("app_configuration_versions")}
        fks = {tuple(f["constrained_columns"]) for f in inspect(bind).get_foreign_keys("app_configuration_versions")}
        with op.batch_alter_table("app_configuration_versions", recreate=recreate, naming_convention=NAMING) as batch:
            if "source_version_id" not in cols: batch.add_column(sa.Column("source_version_id",sa.String(36),nullable=True))
            batch.alter_column("active",existing_type=sa.Boolean(),nullable=False,server_default=sa.true())
            batch.alter_column("created_at",existing_type=sa.DateTime(),nullable=False,server_default=sa.text("CURRENT_TIMESTAMP"))
            if ("tenant_id",) not in fks: batch.create_foreign_key("fk_app_config_tenant","tenants",["tenant_id"],["id"])
            if ("originating_change_set_id",) not in fks: batch.create_foreign_key("fk_app_config_originating_change_set","dashboard_change_sets",["originating_change_set_id"],["id"])
            if ("created_by",) not in fks: batch.create_foreign_key("fk_app_config_created_by","app_users",["created_by"],["id"])
            if ("source_version_id",) not in fks: batch.create_foreign_key("fk_app_config_source_version","app_configuration_versions",["source_version_id"],["id"])
    if "dashboard_change_sets" in original:
        fks = {tuple(f["constrained_columns"]) for f in inspect(bind).get_foreign_keys("dashboard_change_sets")}
        with op.batch_alter_table("dashboard_change_sets", recreate=recreate, naming_convention=NAMING) as batch:
            batch.alter_column("validation_json",existing_type=sa.Text(),nullable=False,server_default=sa.text("'{}'"))
            batch.alter_column("status",existing_type=sa.String(30),nullable=False,server_default=sa.text("'proposed'"))
            batch.alter_column("rollback_json",existing_type=sa.Text(),nullable=False,server_default=sa.text("'{}'"))
            batch.alter_column("created_at",existing_type=sa.DateTime(),nullable=False,server_default=sa.text("CURRENT_TIMESTAMP"))
            batch.alter_column("updated_at",existing_type=sa.DateTime(),nullable=False,server_default=sa.text("CURRENT_TIMESTAMP"))
            if ("tenant_id",) not in fks: batch.create_foreign_key("fk_dashboard_change_set_tenant","tenants",["tenant_id"],["id"])
            if ("created_by",) not in fks: batch.create_foreign_key("fk_dashboard_change_set_created_by","app_users",["created_by"],["id"])
            if ("applied_version_id",) not in fks: batch.create_foreign_key("fk_dashboard_change_set_applied_version","app_configuration_versions",["applied_version_id"],["id"])
    if "dashboard_customizations" in original:
        fks = {tuple(f["constrained_columns"]) for f in inspect(bind).get_foreign_keys("dashboard_customizations")}
        with op.batch_alter_table("dashboard_customizations", recreate=recreate, naming_convention=NAMING) as batch:
            batch.alter_column("override_json",existing_type=sa.Text(),nullable=False,server_default=sa.text("'{}'"))
            batch.alter_column("updated_at",existing_type=sa.DateTime(),nullable=False,server_default=sa.text("CURRENT_TIMESTAMP"))
            if ("tenant_id",) not in fks: batch.create_foreign_key("fk_dashboard_customization_tenant","tenants",["tenant_id"],["id"])
            if ("updated_by",) not in fks: batch.create_foreign_key("fk_dashboard_customization_updated_by","app_users",["updated_by"],["id"])
    if "dashboard_change_operations" in original:
        fks = {tuple(f["constrained_columns"]) for f in inspect(bind).get_foreign_keys("dashboard_change_operations")}
        uniques = {tuple(u["column_names"]) for u in inspect(bind).get_unique_constraints("dashboard_change_operations")}
        with op.batch_alter_table("dashboard_change_operations", recreate=recreate, naming_convention=NAMING) as batch:
            if ("tenant_id",) not in fks: batch.create_foreign_key("fk_dashboard_operation_tenant","tenants",["tenant_id"],["id"])
            if ("change_set_id",) not in fks: batch.create_foreign_key("fk_dashboard_operation_change_set","dashboard_change_sets",["change_set_id"],["id"],ondelete="CASCADE")
            if ("change_set_id","position") not in uniques: batch.create_unique_constraint("uq_dashboard_change_operation_position",["change_set_id","position"])
    if "dashboard_studio_audits" in original:
        fks = {tuple(f["constrained_columns"]) for f in inspect(bind).get_foreign_keys("dashboard_studio_audits")}
        with op.batch_alter_table("dashboard_studio_audits", recreate=recreate, naming_convention=NAMING) as batch:
            batch.alter_column("details_json",existing_type=sa.Text(),nullable=False,server_default=sa.text("'{}'"))
            batch.alter_column("created_at",existing_type=sa.DateTime(),nullable=False,server_default=sa.text("CURRENT_TIMESTAMP"))
            if ("tenant_id",) not in fks: batch.create_foreign_key("fk_dashboard_audit_tenant","tenants",["tenant_id"],["id"])
            if ("actor_id",) not in fks: batch.create_foreign_key("fk_dashboard_audit_actor","app_users",["actor_id"],["id"])


def _ensure_indexes(bind):
    inspector=inspect(bind)
    for table,indexes in INDEXES.items():
        existing={item["name"] for item in inspector.get_indexes(table)}
        for name,columns in indexes:
            if name not in existing:op.create_index(name,table,columns,unique=False)


def upgrade() -> None:
    bind = op.get_bind()
    original = set(inspect(bind).get_table_names()) & set(TABLES)
    _validate_and_normalize(bind, original)
    _create_missing(bind, original)
    # Reinspect every resulting table so dialects that defer cyclic foreign keys
    # receive the same final constraints as upgraded legacy tables.
    _upgrade_existing(bind, set(TABLES))
    _ensure_indexes(bind)


def downgrade() -> None:
    raise RuntimeError("Dashboard Studio downgrade is unsafe; restore a verified pre-migration backup")
