"""Regression gate for Operly-only business authority and DragonZpyder AI."""
import unittest
from pathlib import Path


class DragonZpyderOnlyRuntimeTests(unittest.TestCase):
    def test_chat_routes_never_reenter_local_model(self):
        src = Path("apps/api/agent_runtime_router.py").read_text()
        self.assertIn("run_personal_harness(", src)
        self.assertIn("run_workspace_harness(", src)
        for old in (
            "Runtime1Agent", "OpenAICompatibleAgentModel",
            "get_agent_service", "get_personal_agent_service",
            "collect_inference_usage", "build_personal_runtime",
            "build_workspace_runtime", "OPERLY_AI_EXECUTION_OWNER",
            "OPERLY_RETIRE_LOCAL_AGENT_EXECUTION",
        ):
            self.assertNotIn(old, src)
        main = Path("apps/api/main.py").read_text()
        self.assertNotIn("run_startup_objective_eval_if_enabled", main)

    def test_workspace_permissions_still_enforce_remote_tools(self):
        self.assertIn("issue_workspace_delegation(", Path(
            "packages/agent_runtime/dragonzpyder_workspace.py").read_text())
        self.assertIn("issue_personal_delegation(", Path(
            "packages/agent_runtime/dragonzpyder_personal.py").read_text())
        self.assertTrue(Path("packages/mcp/first_party_workspace.py").exists())
        self.assertTrue(Path("packages/security/permissions.py").exists())
        self.assertIn("context.is_guest_workspace", Path(
            "apps/api/agent_runtime_router.py").read_text())

    def test_discord_does_not_fallback_to_independent_operly_agent(self):
        source = Path(
            "packages/workspace_modules/integrations/discord/bot.py"
        ).read_text()
        self.assertNotIn("Runtime1Agent", source)
        self.assertNotIn("OpenAICompatibleAgentModel", source)
        self.assertNotIn("evaluate_discord_request", source)
        self.assertNotIn("build_personal_runtime", source)
        self.assertNotIn("build_workspace_runtime", source)
        self.assertIn("async def _handle_command(", source)
        self.assertIn("async def _bind_workspace(", source)
        self.assertIn("async def on_message(", source)
        status = Path(
            "packages/workspace_modules/integrations/discord/runtime_status.py"
        ).read_text()
        self.assertNotIn("InferenceRoute", status)
        self.assertIn("return False", status)

    def test_uploaded_handles_are_committed_before_remote_mcp(self):
        source = Path("apps/api/agent_runtime_router.py").read_text()
        fragment = source.split("async def _save_uploads(", 1)[1].split(
            "@workspace_router.post", 1)[0]
        self.assertIn("await db.commit()", fragment)
        self.assertIn("await db.commit()", source)


if __name__ == "__main__":
    unittest.main()
