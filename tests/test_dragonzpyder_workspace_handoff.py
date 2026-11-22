"""Workspace delegation retains Operly membership scope without local fallback."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from apps.api.agent_runtime_router import _use_dragonzpyder_workspace_harness
from packages.agent_runtime.dragonzpyder_workspace import (
    DragonZpyderWorkspaceUnavailable, _endpoint,
)


class WorkspaceHandoffTests(unittest.TestCase):
    def context(self, user="alice", guest=False, personal=False):
        return SimpleNamespace(
            is_workspace=not personal, is_guest_workspace=guest,
            is_personal=personal, user_id=user,
            workspace_id=None if personal else "workspace-123",
        )

    def test_all_full_members_delegate_and_guest_isolation_is_kept(self):
        with patch.dict("os.environ", {
            "OPERLY_WORKSPACE_HARNESS_OWNER": "operly",
            "OPERLY_WORKSPACE_DRAGONZPYDER_ALL_USERS_ENABLED": "0",
        }, clear=True):
            self.assertTrue(_use_dragonzpyder_workspace_harness(self.context()))
            self.assertTrue(_use_dragonzpyder_workspace_harness(self.context(user="bob")))
            self.assertFalse(_use_dragonzpyder_workspace_harness(self.context(user=None)))
            self.assertFalse(_use_dragonzpyder_workspace_harness(self.context(guest=True)))
            self.assertFalse(_use_dragonzpyder_workspace_harness(self.context(personal=True)))

    def test_endpoint_allowlist(self):
        target = "https://api.dragonzpyder.xyz/internal/agents/operly/workspace/run"
        with patch.dict("os.environ", {"OPERLY_DRAGONZPYDER_WORKSPACE_RUN_URL": target}, clear=True):
            self.assertEqual(_endpoint(), target)
        for target in (
            "https://evil.example/internal/agents/operly/workspace/run",
            "https://api.dragonzpyder.xyz/internal/agents/operly/personal/run",
            "https://api.dragonzpyder.xyz/internal/agents/operly/workspace/run?token=x",
        ):
            with patch.dict("os.environ", {"OPERLY_DRAGONZPYDER_WORKSPACE_RUN_URL": target}, clear=True):
                with self.assertRaises(DragonZpyderWorkspaceUnavailable):
                    _endpoint()


if __name__ == "__main__":
    unittest.main()
