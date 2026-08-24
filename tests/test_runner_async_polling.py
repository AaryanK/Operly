import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from packages.coding_harness.execution_loop import _await_runner_build


class AsyncRunnerPollingTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_runner_ack_is_not_treated_as_finished_failure(self):
        queued = SimpleNamespace(id="build-1", state="queued")
        building = SimpleNamespace(id="build-1", state="building")
        ready = SimpleNamespace(id="build-1", state="preview_ready")
        refresh = AsyncMock(side_effect=[building, ready])

        with patch(
            "packages.coding_harness.execution_loop.refresh_build",
            new=refresh,
        ), patch(
            "packages.coding_harness.execution_loop._runner_poll_interval",
            return_value=0.001,
        ):
            result = await _await_runner_build(object(), queued, adapter=object())

        self.assertIs(result, ready)
        self.assertEqual(refresh.await_count, 2)

    async def test_evidence_bearing_failure_returns_without_false_preview(self):
        queued = SimpleNamespace(id="build-2", state="queued")
        failed = SimpleNamespace(id="build-2", state="tests_failed")
        refresh = AsyncMock(return_value=failed)

        with patch(
            "packages.coding_harness.execution_loop.refresh_build",
            new=refresh,
        ), patch(
            "packages.coding_harness.execution_loop._runner_poll_interval",
            return_value=0.001,
        ):
            result = await _await_runner_build(object(), queued, adapter=object())

        self.assertIs(result, failed)
        self.assertEqual(result.state, "tests_failed")


if __name__ == "__main__":
    unittest.main()
