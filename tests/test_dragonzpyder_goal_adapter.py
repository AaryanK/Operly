"""No-network tests: Operly is only a scoped adapter to DragonZpyder goals."""
import unittest
from unittest.mock import patch, AsyncMock

from packages.workflow.dragonzpyder_goals import (
    register_goal, read_goal, cancel_goal,
)
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


def context(kind=ScopeKind.WORKSPACE):
    return ExecutionContext(
        workspace_id="workspace-1" if kind is ScopeKind.WORKSPACE else None,
        user_id="user-1", membership_id="member-1" if kind is ScopeKind.WORKSPACE else None,
        role="owner", permissions=frozenset({"workflows:write"}),
        channel="web", surface=(
            SurfaceKind.WORKSPACE_PRIVATE if kind is ScopeKind.WORKSPACE
            else SurfaceKind.PERSONAL_PRIVATE
        ), scope_kind=kind, principal_id="user:user-1",
    )


class GoalAdapterContracts(unittest.IsolatedAsyncioTestCase):
    async def test_workspace_goal_request_uses_context_not_model_identity(self):
        with patch("packages.workflow.dragonzpyder_goals._call",
                   new_callable=AsyncMock, return_value={
                       "goal_id":"goal-1", "status":"registered",
                       "execution_started":False, "job_registered":False,
                   }) as remote:
            result = await register_goal(
                context=context(), request_id="test-request-1",
                goal="Monitor the project", schedule=None,
            )
        self.assertFalse(result["execution_started"])
        remote.assert_awaited_once()
        payload = remote.await_args.args[1]
        self.assertEqual(payload["kind"], "workspace")
        self.assertEqual(payload["workspace_id"], "workspace-1")
        self.assertEqual(payload["user_id"], "user-1")

    async def test_personal_goal_cannot_grant_workspace_context(self):
        with patch("packages.workflow.dragonzpyder_goals._call",
                   new_callable=AsyncMock, return_value={
                       "goal_id":"goal-p", "status":"registered",
                   }) as remote:
            await read_goal(context=context(ScopeKind.PERSONAL), goal_id="goal-p")
        payload = remote.await_args.args[1]
        self.assertEqual(payload["kind"], "personal")
        self.assertIsNone(payload["workspace_id"])

    async def test_goal_cancellation_is_delegated_not_locally_scheduled(self):
        with patch("packages.workflow.dragonzpyder_goals._call",
                   new_callable=AsyncMock, return_value={
                       "goal_id":"goal-w", "status":"cancelled",
                   }) as remote:
            await cancel_goal(context=context(), goal_id="goal-w")
        self.assertEqual(remote.await_args.args[0], "cancel")


if __name__ == "__main__":
    unittest.main()
