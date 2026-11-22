"""Inventory cannot accidentally export private user records."""
import asyncio
import unittest

from scripts.dragonzpyder_migration_inventory import TABLES, snapshot_counts


class DummyResult:
    def __init__(self, rows):
        self.rows = rows
    def all(self):
        return self.rows


class DummySession:
    def __init__(self):
        self.sql = []
    async def scalar(self, query):
        sql = str(query)
        self.sql.append(sql)
        if "WHERE active = TRUE" in sql:
            return 2
        return 3
    async def execute(self, query):
        self.sql.append(str(query))
        return DummyResult([("completed", 2), ("waiting_approval", 1)])


class InventoryTests(unittest.TestCase):
    def test_counts_only_never_select_sensitive_columns(self):
        db = DummySession()
        report = asyncio.run(snapshot_counts(db))
        self.assertEqual(report["counts"]["accounts"], 3)
        self.assertEqual(report["workflow_run_status_counts"]["completed"], 2)
        self.assertEqual(report["active_accounts"], 2)
        self.assertFalse(report["backups_verified"])
        self.assertFalse(report["production_activation_authorized"])
        self.assertEqual(len(db.sql), len(TABLES) + 2)
        self.assertTrue(all(
            s.startswith("SELECT COUNT(*) FROM ") or
            s.startswith("SELECT status, COUNT(*) FROM workflow_runs")
            for s in db.sql
        ))
        self.assertNotIn("password_hash", str(report))
        self.assertNotIn("access_token", str(report))


if __name__ == "__main__":
    unittest.main()
