import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from alembic import command

from packages.database.migrate import config, revisions
from packages.database.schema import ALEMBIC_HEAD


def _url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def test_solution_generation_lease_migration_upgrades_fresh_database():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "operly.db"
        url = _url(path)

        command.upgrade(config(url), "head")

        # This test owns the 0038 Solution lease columns, not the repository's global
        # migration head. Future additive migrations must not require editing this
        # fixture merely because they extend the same linear Alembic chain.
        current, head = revisions(url)
        assert current == ALEMBIC_HEAD
        assert head == ALEMBIC_HEAD
        with closing(sqlite3.connect(path)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(solution_jobs)")}
            assert {
                "created_by",
                "plan_id",
                "locked_by",
                "lease_expires_at",
                "heartbeat_at",
            } <= columns
            indexes = {row[1] for row in db.execute("PRAGMA index_list(solution_jobs)")}
            assert {
                "ix_solution_jobs_created_by",
                "ix_solution_jobs_plan_id",
                "ix_solution_jobs_locked_by",
                "ix_solution_jobs_lease_expires_at",
            } <= indexes
            foreign_columns = {
                row[3] for row in db.execute("PRAGMA foreign_key_list(solution_jobs)")
            }
            assert {"created_by", "plan_id"} <= foreign_columns
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []