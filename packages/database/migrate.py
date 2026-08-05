"""Revisioned migration, validation, backup, and release-gate commands."""
import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from contextlib import closing

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from packages.database.backup import sqlite_path, verified_backup,verified_postgres_dump
from packages.database.db import normalize_database_url
from packages.database.schema import synchronous_database_url

REQUIRED_TABLES = {"dashboard_customizations","dashboard_change_sets","dashboard_change_operations","app_configuration_versions","dashboard_studio_audits"}
REQUIRED_INDEXES = {
    "dashboard_customizations":{"ix_dashboard_customizations_tenant_id","ix_dashboard_customizations_screen_id","ix_dashboard_customizations_component_id"},
    "dashboard_change_sets":{"ix_dashboard_change_sets_tenant_id","ix_dashboard_change_sets_screen_id","ix_dashboard_change_sets_status"},
    "dashboard_change_operations":{"ix_dashboard_change_operations_tenant_id","ix_dashboard_change_operations_change_set_id"},
    "app_configuration_versions":{"ix_app_configuration_versions_tenant_id","ix_app_configuration_versions_active"},
    "dashboard_studio_audits":{"ix_dashboard_studio_audits_tenant_id"},
}
REQUIRED_COLUMNS={
    "dashboard_customizations":{"id","tenant_id","screen_id","component_id","override_json","updated_by","updated_at"},
    "dashboard_change_sets":{"id","tenant_id","screen_id","before_json","after_json","status","created_by","applied_version_id","created_at","updated_at"},
    "dashboard_change_operations":{"id","tenant_id","change_set_id","position","operation","component_id","changes_json"},
    "app_configuration_versions":{"id","tenant_id","version_number","snapshot_json","originating_change_set_id","source_version_id","created_by","active","created_at"},
    "dashboard_studio_audits":{"id","tenant_id","actor_id","action","created_at"},
}
REQUIRED_FOREIGN_KEYS={
    "dashboard_customizations":{"tenant_id","updated_by"},
    "dashboard_change_sets":{"tenant_id","created_by","applied_version_id"},
    "dashboard_change_operations":{"tenant_id","change_set_id"},
    "app_configuration_versions":{"tenant_id","originating_change_set_id","source_version_id","created_by"},
    "dashboard_studio_audits":{"tenant_id","actor_id"},
}
BUILDER_REQUIRED_TABLES={"managed_applications","application_versions","application_change_sets","managed_records","application_audit_events","application_preview_sessions"}
BUILDER_REQUIRED_INDEXES={
    "managed_applications":{"ix_managed_applications_tenant_id"},
    "application_versions":{"ix_application_versions_tenant_id","ix_application_versions_application_id","ix_application_versions_active"},
    "application_change_sets":{"ix_application_change_sets_tenant_id","ix_application_change_sets_application_id","ix_application_change_sets_status"},
    "managed_records":{"ix_managed_records_tenant_id","ix_managed_records_application_id","ix_managed_records_entity_id"},
    "application_audit_events":{"ix_application_audit_events_tenant_id","ix_application_audit_events_application_id","ix_application_audit_events_action"},
    "application_preview_sessions":{"ix_application_preview_sessions_tenant_id","ix_application_preview_sessions_application_id"},
}
BUILDER_REQUIRED_FOREIGN_KEYS={
    "managed_applications":{"tenant_id","created_by"},"application_versions":{"tenant_id","application_id","source_version_id","created_by"},
    "application_change_sets":{"tenant_id","application_id","base_version_id","applied_version_id","created_by"},"managed_records":{"tenant_id","application_id","created_by"},
    "application_audit_events":{"tenant_id","application_id"},"application_preview_sessions":{"tenant_id","application_id","change_set_id","created_by"},
}
CUSTOM_REQUIRED_TABLES={"generated_projects","generated_project_change_sets","service_customers","service_requests","service_status_events"}
POSTGRES_BACKUP_MAX_AGE = timedelta(hours=24)
POSTGRES_BACKUP_CLOCK_SKEW = timedelta(minutes=5)


def database_url(override: str | None = None) -> str:
    return normalize_database_url(override or os.getenv("DATABASE_URL","sqlite+aiosqlite:///./operly.db"))


def config(url: str) -> Config:
    cfg=Config(str(Path(__file__).resolve().parents[2]/"alembic.ini"))
    cfg.set_main_option("sqlalchemy.url",url.replace("%","%%"))
    return cfg


def revisions(url: str) -> tuple[str | None,str]:
    cfg=config(url);script=ScriptDirectory.from_config(cfg);head=script.get_current_head()
    engine=create_engine(synchronous_database_url(url))
    try:
        with engine.connect() as connection:current=MigrationContext.configure(connection).get_current_revision()
        return current,head
    finally:engine.dispose()


def is_postgres(url:str)->bool:return url.startswith(("postgres://","postgresql://","postgresql+asyncpg://","postgresql+psycopg://"))


def release_id(value: str | None = None) -> str:
    result=(value or os.getenv("OPERLY_RELEASE_ID") or os.getenv("RAILWAY_DEPLOYMENT_ID") or "").strip()
    if not result:raise RuntimeError("A deployment/release identifier is required for PostgreSQL migration")
    return result


def development_unbacked_postgres_migration_allowed() -> bool:
    environment=os.getenv("OPERLY_ENV", "").strip().lower()
    confirmed=os.getenv("OPERLY_ALLOW_UNBACKED_POSTGRES_MIGRATIONS", "").strip().lower()
    return environment in {"development", "dev"} and confirmed in {"1", "true", "yes"}


def verify_postgres_backup_confirmation(
    intended_release_id: str,
    confirmed_release_id: str | None = None,
    backup_timestamp: str | None = None,
    now: datetime | None = None,
) -> None:
    confirmed=(confirmed_release_id or os.getenv("OPERLY_POSTGRES_BACKUP_RELEASE_ID") or "").strip()
    timestamp=(backup_timestamp or os.getenv("OPERLY_POSTGRES_BACKUP_AT") or "").strip()
    if not confirmed or not timestamp:
        raise RuntimeError("A release-scoped PostgreSQL backup confirmation is required")
    if confirmed != intended_release_id:
        raise RuntimeError("PostgreSQL backup confirmation belongs to a different release")
    try:
        backed_up_at=datetime.fromisoformat(timestamp.replace("Z","+00:00"))
    except ValueError as error:
        raise RuntimeError("PostgreSQL backup timestamp must be ISO 8601") from error
    if backed_up_at.tzinfo is None:
        raise RuntimeError("PostgreSQL backup timestamp must include a timezone")
    current=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    backed_up_at=backed_up_at.astimezone(timezone.utc)
    if backed_up_at > current + POSTGRES_BACKUP_CLOCK_SKEW:
        raise RuntimeError("PostgreSQL backup timestamp is in the future")
    if current - backed_up_at > POSTGRES_BACKUP_MAX_AGE:
        raise RuntimeError("PostgreSQL backup confirmation is stale")


def inspect_supported_schema(url:str)->str:
    from packages.database.db import Base
    from packages.database.schema import import_all_models
    import_all_models()
    engine=create_engine(synchronous_database_url(url))
    try:
        with engine.connect() as connection:
            inspector=inspect(connection);tables=set(inspector.get_table_names())-{"alembic_version"}
            current=MigrationContext.configure(connection).get_current_revision()
            if current not in {None,"0001_operly_core","0002_dashboard_studio","0003_application_builder_core","0004_managed_record_runtime","0005_custom_software_vertical_slice"}:raise RuntimeError("Unsupported Alembic revision")
            if not tables:return "fresh"
            if not {"tenants","app_users"}<=tables:raise RuntimeError("Unsupported schema: core identity tables are incomplete")
            modeled={table.name:table for table in Base.metadata.tables.values()}
            for table in tables&set(modeled):
                actual={item["name"] for item in inspector.get_columns(table)}
                expected={column.name for column in modeled[table].columns}
                allowed_missing={"source_version_id"} if table=="app_configuration_versions" else set()
                if (expected-allowed_missing)-actual:raise RuntimeError(f"Unsupported schema: modeled columns are missing from {table}")
            studio=tables&REQUIRED_TABLES
            if not studio:return "legacy_core"
            for table in studio:
                columns={item["name"] for item in inspector.get_columns(table)}
                minimum=(REQUIRED_COLUMNS[table]-{"source_version_id"})
                if not minimum<=columns:raise RuntimeError(f"Unsupported partial schema: required legacy columns are missing from {table}")
            return "versioned" if current else ("legacy_studio" if studio==REQUIRED_TABLES else "partial_studio")
    finally:engine.dispose()


def validate(url: str) -> None:
    current,head=revisions(url)
    if current!=head:raise RuntimeError(f"Database revision is not at head (current={current or 'unversioned'}, head={head})")
    sync=synchronous_database_url(url);engine=create_engine(sync)
    try:
        with engine.connect() as connection:
            inspector=inspect(connection);tables=set(inspector.get_table_names())
            missing=(REQUIRED_TABLES|BUILDER_REQUIRED_TABLES|CUSTOM_REQUIRED_TABLES)-tables
            if missing:raise RuntimeError("Required Studio application tables are missing")
            for table,expected in REQUIRED_INDEXES.items():
                columns={x["name"] for x in inspector.get_columns(table)}
                if REQUIRED_COLUMNS[table]-columns:raise RuntimeError(f"Required columns are missing from {table}")
                actual={x["name"] for x in inspector.get_indexes(table)}
                if expected-actual:raise RuntimeError(f"Required indexes are missing from {table}")
                foreign_columns={column for fk in inspector.get_foreign_keys(table) for column in fk["constrained_columns"]}
                if REQUIRED_FOREIGN_KEYS[table]-foreign_columns:raise RuntimeError(f"Required foreign keys are missing from {table}")
            for table,expected in BUILDER_REQUIRED_INDEXES.items():
                actual={x["name"] for x in inspector.get_indexes(table)}
                if expected-actual:raise RuntimeError(f"Required indexes are missing from {table}")
                foreign_columns={column for fk in inspector.get_foreign_keys(table) for column in fk["constrained_columns"]}
                if BUILDER_REQUIRED_FOREIGN_KEYS[table]-foreign_columns:raise RuntimeError(f"Required foreign keys are missing from {table}")
            uniques={tuple(item["column_names"]) for item in inspector.get_unique_constraints("dashboard_change_operations")}
            if ("change_set_id","position") not in uniques:raise RuntimeError("ChangeSet operation-position uniqueness is missing")
            duplicates=connection.execute(text("SELECT count(*) FROM (SELECT tenant_id FROM app_configuration_versions WHERE active IS TRUE GROUP BY tenant_id HAVING count(*)>1) AS duplicates")).scalar_one()
            if duplicates:raise RuntimeError("Duplicate active configuration versions remain")
            if connection.dialect.name=="sqlite":
                if connection.exec_driver_sql("PRAGMA integrity_check").scalar_one()!="ok":raise RuntimeError("SQLite integrity_check failed")
                if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone():raise RuntimeError("SQLite foreign_key_check failed")
    finally:engine.dispose()


def run(argv=None) -> int:
    parser=argparse.ArgumentParser(description="Operly revisioned database migrations")
    parser.add_argument("command",choices=["current","history","upgrade","check","backup","release-check","deploy-upgrade"])
    parser.add_argument("--database-url")
    parser.add_argument("--backup-path")
    parser.add_argument("--backup-dir")
    parser.add_argument("--release-id")
    parser.add_argument("--postgres-backup-release-id")
    parser.add_argument("--postgres-backup-at")
    parser.add_argument("--allow-production",action="store_true")
    args=parser.parse_args(argv);url=database_url(args.database_url);cfg=config(url)
    if args.command=="current":
        current,head=revisions(url);print(f"current={current or 'unversioned'} head={head}")
    elif args.command=="history":command.history(cfg,verbose=True)
    elif args.command=="upgrade":
        if os.getenv("OPERLY_ENV","development").lower() in {"production","prod"} and not args.allow_production:raise RuntimeError("Production upgrade requires --allow-production after a verified backup")
        command.upgrade(cfg,"head");validate(url);print("upgrade complete; database is at head")
    elif args.command=="deploy-upgrade":
        if not args.allow_production:raise RuntimeError("deploy-upgrade requires --allow-production")
        state=inspect_supported_schema(url);print(f"recognized schema state: {state}")
        if is_postgres(url):
            if development_unbacked_postgres_migration_allowed():
                print("development-only unbacked PostgreSQL migration explicitly authorized")
            else:
                intended_release=release_id(args.release_id)
                if args.backup_dir:
                    backup=verified_postgres_dump(url,Path(args.backup_dir));print(f"verified PostgreSQL dump created: {backup}")
                else:
                    verify_postgres_backup_confirmation(intended_release,args.postgres_backup_release_id,args.postgres_backup_at)
        else:
            backup=verified_backup(url,Path(args.backup_dir) if args.backup_dir else None);print(f"verified backup created: {backup}")
        command.upgrade(cfg,"head");validate(url);print("controlled production upgrade complete")
    elif args.command=="check":validate(url);print("schema check passed")
    elif args.command=="backup":
        if is_postgres(url):
            if not args.backup_dir:raise RuntimeError("PostgreSQL pg_dump requires --backup-dir")
            print(verified_postgres_dump(url,Path(args.backup_dir)))
        else:print(verified_backup(url,Path(args.backup_dir) if args.backup_dir else None))
    else:
        if is_postgres(url):
            verify_postgres_backup_confirmation(release_id(args.release_id),args.postgres_backup_release_id,args.postgres_backup_at)
        else:
            if not args.backup_path:raise RuntimeError("release-check requires --backup-path")
            backup=Path(args.backup_path).resolve();expected=sqlite_path(url)
            if not backup.is_file() or backup==expected:raise RuntimeError("A separate verified backup is required")
            with closing(sqlite3.connect(backup)) as connection:
                if connection.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise RuntimeError("Backup integrity_check failed")
        validate(url);print("migration release gate passed")
    return 0


if __name__=="__main__":
    try:raise SystemExit(run())
    except Exception as error:
        print(f"migration command failed: {error}")
        raise SystemExit(1)
