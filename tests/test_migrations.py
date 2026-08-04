import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from alembic import command
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.requests import Request
from starlette.responses import JSONResponse

from apps.api.security_headers import SecurityHeadersMiddleware
from packages.database.backup import verified_backup,verified_postgres_dump
from packages.database.db import assert_schema_current
from packages.database.migrate import config, development_unbacked_postgres_migration_allowed, release_id, revisions, validate, verify_postgres_backup_confirmation
from scripts.run_web import port as web_port


def url(path):return f"sqlite+aiosqlite:///{path.as_posix()}"


def legacy_core(path):
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
        CREATE TABLE tenants(id VARCHAR(36) PRIMARY KEY,name VARCHAR(200) NOT NULL,slug VARCHAR(100),timezone VARCHAR(100),created_at DATETIME);
        CREATE TABLE app_users(id VARCHAR(36) PRIMARY KEY,email VARCHAR(320) NOT NULL UNIQUE,password_hash TEXT NOT NULL,display_name VARCHAR(200),active BOOLEAN,created_at DATETIME);
        INSERT INTO tenants VALUES('tenant-a','Existing workspace','existing','UTC',CURRENT_TIMESTAMP);
        INSERT INTO app_users VALUES('user-a','existing@example.test','hash','Owner',1,CURRENT_TIMESTAMP);
        """)
        db.commit()


def partial_studio(path,orphan=False,duplicate_active=False):
    legacy_core(path)
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
        CREATE TABLE dashboard_customizations(id VARCHAR(36) PRIMARY KEY,tenant_id VARCHAR(36) NOT NULL,screen_id VARCHAR(100) NOT NULL,component_id VARCHAR(120) NOT NULL,override_json TEXT NOT NULL,updated_by VARCHAR(36) NOT NULL,updated_at DATETIME,UNIQUE(tenant_id,screen_id,component_id));
        CREATE TABLE dashboard_change_sets(id VARCHAR(36) PRIMARY KEY,tenant_id VARCHAR(36) NOT NULL,screen_id VARCHAR(100) NOT NULL,originating_chat_message TEXT NOT NULL,target_component_ids_json TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,explanation TEXT NOT NULL,validation_json TEXT NOT NULL,status VARCHAR(30) NOT NULL,created_by VARCHAR(36) NOT NULL,applied_version_id VARCHAR(36),rollback_json TEXT NOT NULL,created_at DATETIME,updated_at DATETIME);
        CREATE TABLE dashboard_change_operations(id VARCHAR(36) PRIMARY KEY,tenant_id VARCHAR(36) NOT NULL,change_set_id VARCHAR(36) NOT NULL,position INTEGER NOT NULL,operation VARCHAR(50) NOT NULL,component_id VARCHAR(120) NOT NULL,changes_json TEXT NOT NULL);
        CREATE TABLE app_configuration_versions(id VARCHAR(36) PRIMARY KEY,tenant_id VARCHAR(36) NOT NULL,version_number INTEGER NOT NULL,snapshot_json TEXT NOT NULL,summary VARCHAR(500) NOT NULL,affected_json TEXT NOT NULL,originating_change_set_id VARCHAR(36),created_by VARCHAR(36) NOT NULL,active BOOLEAN NOT NULL,created_at DATETIME);
        CREATE TABLE dashboard_studio_audits(id VARCHAR(36) PRIMARY KEY,tenant_id VARCHAR(36) NOT NULL,actor_id VARCHAR(36) NOT NULL,action VARCHAR(80) NOT NULL,entity_id VARCHAR(36),details_json TEXT NOT NULL,created_at DATETIME);
        INSERT INTO dashboard_change_sets VALUES('change-a','tenant-a','overview','rename','[]','{}','{}','existing','{}','proposed','user-a',NULL,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);
        INSERT INTO app_configuration_versions VALUES('version-a','tenant-a',1,'{}','existing','[]','change-a','user-a',1,CURRENT_TIMESTAMP);
        """)
        if orphan:db.execute("INSERT INTO dashboard_change_operations VALUES('op-a','tenant-a','missing',0,'update_component','overview-messages-card','{}')")
        if duplicate_active:db.execute("INSERT INTO app_configuration_versions VALUES('version-b','tenant-a',2,'{}','newer','[]',NULL,'user-a',1,CURRENT_TIMESTAMP)")
        db.commit()


class MigrationTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def upgrade(self,path):command.upgrade(config(url(path)),"head");validate(url(path))
    def test_fresh_upgrade_and_idempotency(self):
        path=self.root/"fresh.db";self.upgrade(path);self.upgrade(path);self.assertEqual(revisions(url(path))[0],"0003_application_builder_core")
    def test_legacy_core_preserves_rows(self):
        path=self.root/"legacy.db";legacy_core(path);self.upgrade(path)
        with closing(sqlite3.connect(path)) as db:self.assertEqual(db.execute("select count(*) from app_users").fetchone()[0],1);self.assertEqual(db.execute("pragma integrity_check").fetchone()[0],"ok")
    def test_partial_studio_upgrade_and_constraints(self):
        path=self.root/"partial.db";partial_studio(path);self.upgrade(path)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("select count(*) from app_configuration_versions").fetchone()[0],1)
            self.assertEqual(db.execute("pragma foreign_key_check").fetchall(),[])
            self.assertIn("source_version_id",{r[1] for r in db.execute("pragma table_info(app_configuration_versions)")})
            self.assertGreaterEqual(sum(len(db.execute(f"pragma foreign_key_list({t})").fetchall()) for t in ["dashboard_customizations","dashboard_change_sets","dashboard_change_operations","app_configuration_versions","dashboard_studio_audits"]),13)
    def test_duplicate_active_versions_are_normalized(self):
        path=self.root/"duplicates.db";partial_studio(path,duplicate_active=True);self.upgrade(path)
        with closing(sqlite3.connect(path)) as db:self.assertEqual(db.execute("select count(*) from app_configuration_versions where active=1").fetchone()[0],1);self.assertEqual(db.execute("select active from app_configuration_versions where id='version-b'").fetchone()[0],1)
    def test_unsafe_orphan_fails_without_claiming_head(self):
        path=self.root/"orphan.db";partial_studio(path,orphan=True)
        with self.assertRaises(RuntimeError):command.upgrade(config(url(path)),"head")
        self.assertNotEqual(revisions(url(path))[0],"0003_application_builder_core")
    def test_verified_backup_never_overwrites(self):
        path=self.root/"source.db";legacy_core(path);backup=verified_backup(url(path),self.root/"backups");self.assertTrue(backup.is_file())
        with closing(sqlite3.connect(backup)) as db:self.assertEqual(db.execute("pragma integrity_check").fetchone()[0],"ok")
    def test_postgres_backup_never_falls_back_to_file_copy(self):
        with patch("packages.database.backup.shutil.which",return_value=None):
            with self.assertRaisesRegex(RuntimeError,"Railway PostgreSQL backup"):
                verified_postgres_dump("postgresql://user@example.test/operly",self.root)
    def test_postgres_backup_confirmation_is_release_scoped_and_fresh(self):
        now=datetime(2026,8,4,12,tzinfo=timezone.utc)
        verify_postgres_backup_confirmation("release-b","release-b",(now-timedelta(hours=1)).isoformat(),now)
        with self.assertRaisesRegex(RuntimeError,"different release"):
            verify_postgres_backup_confirmation("release-b","release-a",now.isoformat(),now)
        with self.assertRaisesRegex(RuntimeError,"stale"):
            verify_postgres_backup_confirmation("release-b","release-b",(now-timedelta(hours=25)).isoformat(),now)
        with self.assertRaisesRegex(RuntimeError,"timezone"):
            verify_postgres_backup_confirmation("release-b","release-b","2026-08-04T11:00:00",now)
    def test_legacy_boolean_is_not_accepted_as_release_authorization(self):
        with patch.dict(os.environ,{"OPERLY_POSTGRES_BACKUP_CONFIRMED":"yes"},clear=True):
            with self.assertRaisesRegex(RuntimeError,"release identifier"):
                release_id()
    def test_unbacked_postgres_migrations_require_explicit_development_mode(self):
        with patch.dict(os.environ,{"OPERLY_ENV":"development","OPERLY_ALLOW_UNBACKED_POSTGRES_MIGRATIONS":"yes"},clear=True):
            self.assertTrue(development_unbacked_postgres_migration_allowed())
        for environment in ("production", "prod", ""):
            with self.subTest(environment=environment):
                with patch.dict(os.environ,{"OPERLY_ENV":environment,"OPERLY_ALLOW_UNBACKED_POSTGRES_MIGRATIONS":"yes"},clear=True):
                    self.assertFalse(development_unbacked_postgres_migration_allowed())
        with patch.dict(os.environ,{"OPERLY_ENV":"development"},clear=True):
            self.assertFalse(development_unbacked_postgres_migration_allowed())


class StartupRevisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_unversioned_database_is_refused(self):
        engine=create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.connect() as connection:
            with self.assertRaises(RuntimeError):await assert_schema_current(connection)
        await engine.dispose()
    async def test_current_database_is_accepted(self):
        engine=create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.exec_driver_sql("CREATE TABLE alembic_version(version_num VARCHAR(32) NOT NULL)")
            await connection.exec_driver_sql("INSERT INTO alembic_version VALUES('0003_application_builder_core')")
            await assert_schema_current(connection)
        await engine.dispose()


class SecurityHeaderTests(unittest.IsolatedAsyncioTestCase):
    async def response(self,scheme="http",path="/"):
        middleware=SecurityHeadersMiddleware(lambda scope,receive,send:None)
        request=Request({"type":"http","method":"GET","path":path,"headers":[],"scheme":scheme,"server":("operly.example",443 if scheme=="https" else 80),"client":("127.0.0.1",1),"query_string":b""})
        async def next_response(request):return JSONResponse({"ok":True})
        return await middleware.dispatch(request,next_response)
    async def test_standard_headers_and_local_no_hsts(self):
        response=await self.response();self.assertEqual(response.headers["x-content-type-options"],"nosniff");self.assertNotIn("strict-transport-security",response.headers)
    async def test_production_https_hsts_without_subdomains_or_preload(self):
        with patch.dict(os.environ,{"OPERLY_ENV":"production"}):response=await self.response("https")
        self.assertEqual(response.headers["strict-transport-security"],"max-age=31536000")
        self.assertNotIn("includeSubDomains",response.headers["strict-transport-security"]);self.assertNotIn("preload",response.headers["strict-transport-security"])
    async def test_authenticated_studio_preview_allows_only_same_origin_framing(self):
        response=await self.response(path="/apps/application-id/preview")
        self.assertEqual(response.headers["x-frame-options"],"SAMEORIGIN");self.assertIn("frame-ancestors 'self'",response.headers["content-security-policy"])
        ordinary=await self.response(path="/apps/application-id/run")
        self.assertEqual(ordinary.headers["x-frame-options"],"DENY");self.assertIn("frame-ancestors 'none'",ordinary.headers["content-security-policy"])


class WebEntrypointTests(unittest.TestCase):
    def test_railway_port_and_local_default(self):
        with patch.dict(os.environ,{},clear=True):self.assertEqual(web_port(),8000)
        with patch.dict(os.environ,{"PORT":"8080"},clear=True):self.assertEqual(web_port(),8080)
        with patch.dict(os.environ,{"PORT":"not-a-port"},clear=True):
            with self.assertRaisesRegex(RuntimeError,"integer"):web_port()
    def test_railway_healthcheck_hostname_is_trusted(self):
        source=(Path(__file__).resolve().parents[1]/"apps"/"api"/"main.py").read_text(encoding="utf-8")
        self.assertIn('railway_health_host = "healthcheck.railway.app"',source)
