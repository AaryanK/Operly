import os
import unittest

from alembic import command
import psycopg

from packages.database.migrate import config,revisions,validate

URL=os.getenv("TEST_POSTGRES_DATABASE_URL")
ENABLED=bool(URL) and os.getenv("OPERLY_TEST_POSTGRES_DESTRUCTIVE","").lower() in {"1","true","yes"}


def psycopg_url():
    return URL.replace("postgresql+asyncpg://","postgresql://",1).replace("postgresql+psycopg://","postgresql://",1)


@unittest.skipUnless(ENABLED,"isolated destructive PostgreSQL rehearsal URL not configured")
class PostgreSQLMigrationTests(unittest.TestCase):
    def reset(self):
        with psycopg.connect(psycopg_url(),autocommit=True) as connection:
            connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
            connection.execute("CREATE SCHEMA public")
    def setUp(self):self.reset()
    def tearDown(self):self.reset()
    def upgrade(self):command.upgrade(config(URL),"head");validate(URL)
    def core(self):
        with psycopg.connect(psycopg_url()) as db:
            db.execute("CREATE TABLE tenants(id VARCHAR(36) PRIMARY KEY,name VARCHAR(200) NOT NULL,slug VARCHAR(100),timezone VARCHAR(100),created_at TIMESTAMP)")
            db.execute("CREATE TABLE app_users(id VARCHAR(36) PRIMARY KEY,email VARCHAR(320) NOT NULL UNIQUE,password_hash TEXT NOT NULL,display_name VARCHAR(200),active BOOLEAN,created_at TIMESTAMP)")
            db.execute("INSERT INTO tenants VALUES('tenant-a','Existing workspace','existing','UTC',CURRENT_TIMESTAMP)")
            db.execute("INSERT INTO app_users VALUES('user-a','existing@example.test','hash','Owner',TRUE,CURRENT_TIMESTAMP)")
    def partial(self,orphan=False,duplicate=False):
        self.core()
        with psycopg.connect(psycopg_url()) as db:
            db.execute("CREATE TABLE dashboard_customizations(id VARCHAR(36) PRIMARY KEY,tenant_id VARCHAR(36) NOT NULL,screen_id VARCHAR(100) NOT NULL,component_id VARCHAR(120) NOT NULL,override_json TEXT NOT NULL,updated_by VARCHAR(36) NOT NULL,updated_at TIMESTAMP,UNIQUE(tenant_id,screen_id,component_id))")
            db.execute("CREATE TABLE dashboard_change_sets(id VARCHAR(36) PRIMARY KEY,tenant_id VARCHAR(36) NOT NULL,screen_id VARCHAR(100) NOT NULL,originating_chat_message TEXT NOT NULL,target_component_ids_json TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,explanation TEXT NOT NULL,validation_json TEXT NOT NULL,status VARCHAR(30) NOT NULL,created_by VARCHAR(36) NOT NULL,applied_version_id VARCHAR(36),rollback_json TEXT NOT NULL,created_at TIMESTAMP,updated_at TIMESTAMP)")
            db.execute("CREATE TABLE dashboard_change_operations(id VARCHAR(36) PRIMARY KEY,tenant_id VARCHAR(36) NOT NULL,change_set_id VARCHAR(36) NOT NULL,position INTEGER NOT NULL,operation VARCHAR(50) NOT NULL,component_id VARCHAR(120) NOT NULL,changes_json TEXT NOT NULL)")
            db.execute("CREATE TABLE app_configuration_versions(id VARCHAR(36) PRIMARY KEY,tenant_id VARCHAR(36) NOT NULL,version_number INTEGER NOT NULL,snapshot_json TEXT NOT NULL,summary VARCHAR(500) NOT NULL,affected_json TEXT NOT NULL,originating_change_set_id VARCHAR(36),created_by VARCHAR(36) NOT NULL,active BOOLEAN NOT NULL,created_at TIMESTAMP)")
            db.execute("CREATE TABLE dashboard_studio_audits(id VARCHAR(36) PRIMARY KEY,tenant_id VARCHAR(36) NOT NULL,actor_id VARCHAR(36) NOT NULL,action VARCHAR(80) NOT NULL,entity_id VARCHAR(36),details_json TEXT NOT NULL,created_at TIMESTAMP)")
            db.execute("INSERT INTO dashboard_change_sets VALUES('change-a','tenant-a','overview','rename','[]','{}','{}','existing','{}','proposed','user-a',NULL,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
            db.execute("INSERT INTO app_configuration_versions VALUES('version-a','tenant-a',1,'{}','existing','[]','change-a','user-a',TRUE,CURRENT_TIMESTAMP)")
            if orphan:db.execute("INSERT INTO dashboard_change_operations VALUES('op-a','tenant-a','missing',0,'update_component','overview-messages-card','{}')")
            if duplicate:db.execute("INSERT INTO app_configuration_versions VALUES('version-b','tenant-a',2,'{}','newer','[]',NULL,'user-a',TRUE,CURRENT_TIMESTAMP)")
    def test_fresh_and_already_current_idempotency(self):self.upgrade();self.upgrade();self.assertEqual(revisions(URL)[0],"0002_dashboard_studio")
    def test_legacy_core_preserves_rows(self):
        self.core();self.upgrade()
        with psycopg.connect(psycopg_url()) as db:self.assertEqual(db.execute("SELECT count(*) FROM app_users").fetchone()[0],1)
    def test_unversioned_studio_constraints_and_rows(self):
        self.partial();self.upgrade()
        with psycopg.connect(psycopg_url()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM app_configuration_versions").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM information_schema.table_constraints WHERE table_schema='public' AND constraint_type='FOREIGN KEY' AND table_name IN ('dashboard_customizations','dashboard_change_sets','dashboard_change_operations','app_configuration_versions','dashboard_studio_audits')").fetchone()[0],13)
    def test_duplicate_active_normalization(self):
        self.partial(duplicate=True);self.upgrade()
        with psycopg.connect(psycopg_url()) as db:self.assertEqual(db.execute("SELECT count(*) FROM app_configuration_versions WHERE active=TRUE").fetchone()[0],1)
    def test_unsafe_orphan_rolls_back_and_never_claims_head(self):
        self.partial(orphan=True)
        with self.assertRaises(RuntimeError):command.upgrade(config(URL),"head")
        self.assertNotEqual(revisions(URL)[0],"0002_dashboard_studio")
