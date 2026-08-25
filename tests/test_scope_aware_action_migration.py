import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from alembic import command

from packages.database.migrate import config, revisions
from packages.database.schema import ALEMBIC_HEAD


def _url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def test_scope_aware_action_migration_upgrades_fresh_database():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "operly.db"
        url = _url(path)
        command.upgrade(config(url), "head")

        current, head = revisions(url)
        # This test protects the 0039 scope invariants, not a frozen repository
        # head. Later migrations must remain able to upgrade through 0039 while
        # preserving those ownership constraints.
        assert current == head == ALEMBIC_HEAD

        with closing(sqlite3.connect(path)) as db:
            for table, check_name in (
                ("business_actions", "ck_business_actions_scope_owner"),
                ("approvals", "ck_approvals_scope_owner"),
            ):
                columns = {
                    row[1]: row for row in db.execute(f"PRAGMA table_info({table})")
                }
                assert {"tenant_id", "scope_kind", "owner_user_id"} <= set(columns)
                # PRAGMA notnull column is index 3. tenant_id must be nullable after 0039.
                assert columns["tenant_id"][3] == 0
                assert columns["scope_kind"][3] == 1

                indexes = {row[1] for row in db.execute(f"PRAGMA index_list({table})")}
                assert f"ix_{table}_scope_kind" in indexes
                assert f"ix_{table}_owner_user_id" in indexes

                foreign_columns = {
                    row[3] for row in db.execute(f"PRAGMA foreign_key_list({table})")
                }
                assert "owner_user_id" in foreign_columns

                create_sql = db.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()[0]
                assert check_name in create_sql
                assert "scope_kind = 'workspace'" in create_sql
                assert "scope_kind = 'personal'" in create_sql

            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
