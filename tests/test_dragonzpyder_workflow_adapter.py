"""Opt-in Operly workflow wrapper calls DragonZpyder, never its old planner."""
import os
import unittest
from unittest.mock import AsyncMock, patch

from packages.workflow.provider import WorkflowProvider
from packages.workflow.dragonzpyder_client import (
    preview_remote_schedule, RemoteWorkflowUnavailable,
)


class WorkflowHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_opted_in_workflow_preview_calls_dragonzpyder(self):
        schedule = {"type": "daily", "time": "09:00", "timezone": "UTC"}
        remote = {"owner": "dragonzpyder",
                  "schedule": schedule, "occurrences": ["2026-11-10T09:00:00"],
                  "execution_started": False}
        with patch.dict(os.environ, {"OPERLY_WORKFLOW_HARNESS_OWNER": "dragonzpyder"}):
            with patch("packages.workflow.dragonzpyder_client.preview_remote_schedule",
                       new_callable=AsyncMock, return_value=remote) as sender:
                result = await WorkflowProvider()._schedule_preview(None, None, {
                    "schedule": schedule, "count": 1,
                })
        sender.assert_awaited_once_with(schedule=schedule, count=1)
        assert result.value["engine"] == "dragonzpyder"
        assert result.value["occurrences"] == ["2026-11-10T09:00:00"]
        assert result.value["execution_started"] is False

    async def test_remote_failure_does_not_fallback_to_old_workflow_engine(self):
        with patch.dict(os.environ, {"OPERLY_WORKFLOW_HARNESS_OWNER": "dragonzpyder"}):
            with patch("packages.workflow.dragonzpyder_client.preview_remote_schedule",
                       new_callable=AsyncMock, side_effect=RemoteWorkflowUnavailable("offline")):
                with self.assertRaises(RemoteWorkflowUnavailable):
                    await WorkflowProvider()._schedule_preview(None, None, {
                        "schedule": {"type": "cron", "expression": "0 9 * * 1"},
                    })

    async def test_old_preview_remains_backward_compatible_when_flag_off(self):
        with patch.dict(os.environ, {"OPERLY_WORKFLOW_HARNESS_OWNER": "operly"}):
            result = await WorkflowProvider()._schedule_preview(None, None, {
                "schedule": {"type":"manual"}
            })
        assert result.value == {"schedule": None, "occurrences": []}


if __name__ == "__main__":
    unittest.main()
