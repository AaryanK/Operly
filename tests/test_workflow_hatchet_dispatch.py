"""No-network tests for the exclusive workflow-driver cutover."""
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from apps.api import workflow_dispatch_router as driver
from packages.workflow.scheduler import WorkflowScheduler

SECRET = "workflow-dispatch-test-" + "x" * 40


class DriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_driver_remains_default_and_owns_both_loops(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(driver, "workflow_scheduler") as scheduler, \
                patch.object(driver, "workflow_event_dispatcher") as events:
            scheduler.start = AsyncMock()
            scheduler.stop = AsyncMock()
            events.start = AsyncMock()
            events.stop = AsyncMock()
            self.assertEqual(await driver.start_workflow_driver(), "local")
            scheduler.start.assert_awaited_once()
            events.start.assert_awaited_once()
            await driver.stop_workflow_driver("local")
            scheduler.stop.assert_awaited_once()
            events.stop.assert_awaited_once()

    async def test_hatchet_mode_never_starts_or_stops_local_pollers(self):
        with patch.dict(os.environ, {"OPERLY_WORKFLOW_DRIVER": "hatchet",
                    "OPERLY_HATCHET_WORKFLOW_DISPATCH_SECRET": SECRET}, clear=True), \
                patch.object(driver, "DATABASE_URL", "postgresql+asyncpg://fixture"), \
                patch.object(driver, "workflow_scheduler") as scheduler, \
                patch.object(driver, "workflow_event_dispatcher") as events:
            self.assertEqual(await driver.start_workflow_driver(), "hatchet")
            await driver.stop_workflow_driver("hatchet")
            scheduler.start.assert_not_called()
            events.start.assert_not_called()
            scheduler.stop.assert_not_called()
            events.stop.assert_not_called()

    async def test_invalid_mode_or_missing_dedicated_authority_fails_closed(self):
        for env in (
            {"OPERLY_WORKFLOW_DRIVER": "unknown"},
            {"OPERLY_WORKFLOW_DRIVER": "hatchet"},
            {"OPERLY_WORKFLOW_DRIVER": "hatchet",
             "OPERLY_HATCHET_WORKFLOW_DISPATCH_SECRET": SECRET,
             "SESSION_SECRET": SECRET},
        ):
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(driver, "DATABASE_URL", "postgresql+asyncpg://fixture"):
                with self.assertRaises(RuntimeError):
                    await driver.start_workflow_driver()

    async def test_pulse_requires_authentication_and_exclusive_driver(self):
        with patch.dict(os.environ, {
                    "OPERLY_HATCHET_WORKFLOW_DISPATCH_SECRET": SECRET}, clear=True):
            with self.assertRaises(HTTPException) as unauthorized:
                await driver.workflow_dispatch_pulse("wrong")
            self.assertEqual(unauthorized.exception.status_code, 401)
            with self.assertRaises(HTTPException) as active_local:
                await driver.workflow_dispatch_pulse(SECRET)
            self.assertEqual(active_local.exception.status_code, 409)

    async def test_dispatched_batch_waits_and_does_not_spawn_local_loop(self):
        scheduler = WorkflowScheduler()
        scheduler.reconcile = AsyncMock()
        scheduler._claim_runs = AsyncMock(return_value=[("run-a", "lease-a"),
                                                         ("run-b", "lease-b")])
        scheduler._execute_claimed_run = AsyncMock()
        self.assertEqual(await scheduler.dispatch_external_batch(), 2)
        scheduler.reconcile.assert_awaited_once()
        scheduler._claim_runs.assert_awaited_once_with(limit=min(scheduler._max_workers, 25))
        self.assertEqual(scheduler._execute_claimed_run.await_count, 2)
        self.assertIsNone(scheduler._task)
        self.assertEqual(scheduler._workers, set())

    async def test_pulse_obeys_cross_replica_lock_and_reports_claims_not_success(self):
        env = {"OPERLY_WORKFLOW_DRIVER": "hatchet",
               "OPERLY_HATCHET_WORKFLOW_DISPATCH_SECRET": SECRET}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(driver, "DATABASE_URL", "postgresql+asyncpg://fixture"), \
                patch.object(driver, "workflow_scheduler") as scheduler, \
                patch.object(driver, "workflow_event_dispatcher") as events, \
                patch.object(driver, "engine") as engine:
            scheduler.status.return_value = {"running": False}
            events.status.return_value = {"running": False}
            scheduler.dispatch_external_batch = AsyncMock(return_value=2)
            events.tick = AsyncMock(return_value=3)
            connection = MagicMock()
            connection.scalar = AsyncMock(side_effect=[False, True])
            context = MagicMock()
            context.__aenter__ = AsyncMock(return_value=connection)
            context.__aexit__ = AsyncMock(return_value=False)
            engine.begin.return_value = context
            self.assertEqual((await driver.workflow_dispatch_pulse(SECRET))["state"], "busy")
            scheduler.dispatch_external_batch.assert_not_called()
            events.tick.assert_not_called()
            response = await driver.workflow_dispatch_pulse(SECRET)
            self.assertEqual(response, {"state": "dispatched", "claimed_runs": 2,
                                        "events_processed": 3})
            scheduler.dispatch_external_batch.assert_awaited_once()
            events.tick.assert_awaited_once()
