"""Regression coverage: DragonZpyder is a user-scoped Personal canary only."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from apps.api.agent_runtime_router import _use_dragonzpyder_personal_harness


class PersonalHarnessCanaryRoutingTests(unittest.TestCase):
    def test_no_global_routing_without_user_allowlist(self):
        with patch.dict("os.environ", {
            "OPERLY_PERSONAL_HARNESS_OWNER": "dragonzpyder",
            "OPERLY_DRAGONZPYDER_PERSONAL_CANARY_USER_IDS": "",
        }):
            self.assertFalse(_use_dragonzpyder_personal_harness(SimpleNamespace(is_personal=True, user_id="user-a")))

    def test_only_explicit_personal_account_can_use_harness(self):
        with patch.dict("os.environ", {
            "OPERLY_PERSONAL_HARNESS_OWNER": "dragonzpyder",
            "OPERLY_DRAGONZPYDER_PERSONAL_CANARY_USER_IDS": "user-a, user-b",
        }):
            self.assertTrue(_use_dragonzpyder_personal_harness(SimpleNamespace(is_personal=True, user_id="user-a")))
            self.assertFalse(_use_dragonzpyder_personal_harness(SimpleNamespace(is_personal=True, user_id="user-c")))
            self.assertFalse(_use_dragonzpyder_personal_harness(SimpleNamespace(is_personal=False, user_id="user-a")))

    def test_all_users_migration_requires_both_independent_gates(self):
        from packages.agent_runtime.dragonzpyder_personal import DragonZpyderPersonalUnavailable
        personal = SimpleNamespace(is_personal=True, user_id="user-c")
        workspace = SimpleNamespace(is_personal=False, user_id="user-c")
        with patch.dict("os.environ", {
            "OPERLY_PERSONAL_HARNESS_OWNER": "dragonzpyder",
            "OPERLY_DRAGONZPYDER_PERSONAL_CANARY_USER_IDS": "",
            "OPERLY_PERSONAL_DRAGONZPYDER_ALL_USERS_ENABLED": "1",
            "OPERLY_PERSONAL_DRAGONZPYDER_ALL_USERS_APPROVED": "0",
        }, clear=True):
            with self.assertRaises(DragonZpyderPersonalUnavailable):
                _use_dragonzpyder_personal_harness(personal)
            self.assertFalse(_use_dragonzpyder_personal_harness(workspace))
        with patch.dict("os.environ", {
            "OPERLY_PERSONAL_HARNESS_OWNER": "dragonzpyder",
            "OPERLY_PERSONAL_DRAGONZPYDER_ALL_USERS_ENABLED": "1",
            "OPERLY_PERSONAL_DRAGONZPYDER_ALL_USERS_APPROVED": "1",
        }, clear=True):
            self.assertTrue(_use_dragonzpyder_personal_harness(personal))
            self.assertFalse(_use_dragonzpyder_personal_harness(workspace))

    def test_strict_owner_routes_all_personal_accounts_without_allowlist(self):
        from apps.api.agent_runtime_router import _strict_dragonzpyder_only
        from packages.agent_runtime.dragonzpyder_personal import DragonZpyderPersonalUnavailable
        with patch.dict("os.environ", {
            "OPERLY_AI_EXECUTION_OWNER": "dragonzpyder",
            "OPERLY_PERSONAL_HARNESS_OWNER": "dragonzpyder",
            "OPERLY_DRAGONZPYDER_PERSONAL_CANARY_USER_IDS": "",
        }, clear=True):
            self.assertTrue(_strict_dragonzpyder_only())
            self.assertTrue(_use_dragonzpyder_personal_harness(
                SimpleNamespace(is_personal=True, user_id="fresh-user")))
            self.assertFalse(_use_dragonzpyder_personal_harness(
                SimpleNamespace(is_personal=False, user_id="fresh-user")))
        with patch.dict("os.environ", {
            "OPERLY_AI_EXECUTION_OWNER": "dragonzpyder",
            "OPERLY_PERSONAL_HARNESS_OWNER": "operly",
        }, clear=True):
            with self.assertRaises(DragonZpyderPersonalUnavailable):
                _use_dragonzpyder_personal_harness(
                    SimpleNamespace(is_personal=True, user_id="fresh-user"))

    def test_strict_owner_disables_local_llm_and_requires_signed_workspace_context(self):
        import asyncio
        from apps.api.agent_runtime_router import _use_dragonzpyder_workspace_harness
        from packages.agent_runtime.interactive import Runtime1Agent
        from packages.agent_runtime.runtime import AgentRuntimeDisabled
        from packages.agent_runtime.dragonzpyder_workspace import DragonZpyderWorkspaceUnavailable
        context = SimpleNamespace(
            is_workspace=True, is_guest_workspace=False,
            workspace_id="workspace-123", user_id="user-123")
        with patch.dict("os.environ", {
            "OPERLY_AI_EXECUTION_OWNER": "dragonzpyder",
            "OPERLY_WORKSPACE_HARNESS_OWNER": "dragonzpyder",
        }, clear=True):
            self.assertTrue(_use_dragonzpyder_workspace_harness(context))
            with self.assertRaises(AgentRuntimeDisabled):
                asyncio.run(Runtime1Agent.run(
                    object.__new__(Runtime1Agent), None,
                    context=SimpleNamespace(), message="hello",
                    kernel=None, run_id="test"))
        with patch.dict("os.environ", {
            "OPERLY_AI_EXECUTION_OWNER": "dragonzpyder",
            "OPERLY_WORKSPACE_HARNESS_OWNER": "operly",
        }, clear=True):
            with self.assertRaises(DragonZpyderWorkspaceUnavailable):
                _use_dragonzpyder_workspace_harness(context)
        with patch.dict("os.environ", {
            "OPERLY_AI_EXECUTION_OWNER": "dragonzpyder",
            "OPERLY_WORKSPACE_HARNESS_OWNER": "dragonzpyder",
        }, clear=True):
            self.assertFalse(_use_dragonzpyder_workspace_harness(
                SimpleNamespace(is_workspace=True, is_guest_workspace=True,
                                workspace_id="workspace-123", user_id="user-123")))

    def test_old_scheduled_planner_is_rejected_but_business_kernel_is_not_deleted(self):
        from packages.agent_runtime.runtime import GovernedAgentRuntime, AgentRuntimeDisabled
        instance = object.__new__(GovernedAgentRuntime)
        instance.settings = SimpleNamespace(enabled=True)
        with patch.dict("os.environ", {"OPERLY_AI_EXECUTION_OWNER": "dragonzpyder"}):
            with self.assertRaises(AgentRuntimeDisabled):
                instance.require_enabled()

    def test_operly_default_owner_does_not_route(self):
        with patch.dict("os.environ", {
            "OPERLY_PERSONAL_HARNESS_OWNER": "operly",
            "OPERLY_DRAGONZPYDER_PERSONAL_CANARY_USER_IDS": "user-a",
        }):
            self.assertFalse(_use_dragonzpyder_personal_harness(SimpleNamespace(is_personal=True, user_id="user-a")))


if __name__ == "__main__":
    unittest.main()
