import unittest
from pathlib import Path

from packages.kernel.contracts import CapabilityRisk
from packages.workspace_modules.agent_computer.native_tools import computer_native_capabilities


class AgentComputerRuntimeContractTests(unittest.TestCase):
    def test_sota_native_tool_surface_is_present(self):
        specs = {spec.id: spec for spec in computer_native_capabilities()}
        required = {
            "computer.runtime.start",
            "computer.runtime.status",
            "computer.runtime.stop",
            "computer.terminal.exec",
            "computer.python.exec",
            "computer.files.list",
            "computer.files.read",
            "computer.files.write",
            "computer.files.mkdir",
            "computer.files.remove",
            "computer.files.move",
            "computer.files.search",
            "computer.process.list",
            "computer.process.kill",
            "computer.git.status",
            "computer.git.diff",
            "computer.git.exec",
            "computer.web.fetch",
            "computer.web.download",
            "computer.browser.open",
            "computer.browser.navigate",
            "computer.browser.snapshot",
            "computer.browser.click",
            "computer.browser.type",
            "computer.browser.press",
            "computer.browser.evaluate",
            "computer.browser.screenshot",
            "computer.browser.close",
        }
        self.assertEqual(set(specs), required)
        for spec in specs.values():
            self.assertEqual(spec.scopes, frozenset({"workspace"}))
            self.assertEqual(spec.resource_scope, "workspace")
            self.assertEqual(spec.permissions, ("computer:execute",))
            self.assertFalse(spec.approval_required)
            self.assertIn("computer", spec.tags)
            self.assertIn("sandbox", spec.tags)

    def test_browser_interaction_is_marked_more_risky_than_observation(self):
        specs = {spec.id: spec for spec in computer_native_capabilities()}
        self.assertEqual(specs["computer.browser.snapshot"].risk, CapabilityRisk.READ_ONLY)
        self.assertEqual(specs["computer.web.fetch"].risk, CapabilityRisk.READ_ONLY)
        for capability_id in (
            "computer.browser.click",
            "computer.browser.type",
            "computer.browser.press",
            "computer.browser.evaluate",
        ):
            self.assertEqual(specs[capability_id].risk, CapabilityRisk.MEDIUM)

    def test_computer_runtime_is_not_the_api_server_shell(self):
        root = Path(__file__).resolve().parents[1]
        client = (root / "packages" / "workspace_modules" / "agent_computer" / "sandbox.py").read_text(encoding="utf-8")
        provider = (root / "packages" / "workspace_modules" / "agent_computer" / "native_tools.py").read_text(encoding="utf-8")
        router = (root / "packages" / "workspace_modules" / "agent_computer" / "router.py").read_text(encoding="utf-8")
        self.assertIn("OPERLY_AGENT_COMPUTER_RUNNER_URL", client)
        self.assertIn("ComputerRunnerClient", provider)
        self.assertIn("computer.runtime.start", router)
        self.assertNotIn("create_subprocess", client)
        self.assertNotIn("create_subprocess", provider)
        self.assertNotIn("create_subprocess", router)
        self.assertNotIn("subprocess.run", client)
        self.assertNotIn("subprocess.run", provider)
        self.assertNotIn("subprocess.run", router)

    def test_reference_runner_is_development_only_and_has_real_python_browser_tools(self):
        root = Path(__file__).resolve().parents[1]
        runner = (root / "apps" / "computer_runner" / "main.py").read_text(encoding="utf-8")
        dockerfile = (root / "apps" / "computer_runner" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('ENVIRONMENT in {"production", "prod"}', runner)
        self.assertIn("intentionally refuses production", runner)
        self.assertIn('["python3", "-c", code]', runner)
        self.assertIn('["/bin/bash", "-lc", command]', runner)
        self.assertIn("async_playwright", runner)
        self.assertIn("browser.screenshot", runner)
        self.assertIn("playwright install --with-deps chromium", dockerfile)

    def test_computer_runtime_persists_only_an_opaque_backend_handle(self):
        root = Path(__file__).resolve().parents[1]
        models = (root / "packages" / "database" / "agent_computer_models.py").read_text(encoding="utf-8")
        migration = (root / "alembic" / "versions" / "0050_workspace_agent_computer.py").read_text(encoding="utf-8")
        for field in (
            "runtime_session_id",
            "runtime_state",
            "runtime_profile",
            "network_policy",
            "runtime_started_at",
            "runtime_updated_at",
        ):
            self.assertIn(field, models)
            self.assertIn(field, migration)
        for secret_field in ("runtime_token", "provider_secret", "browser_password", "shell_password"):
            self.assertNotIn(secret_field, models)
            self.assertNotIn(secret_field, migration)

    def test_frontend_exposes_general_computer_and_native_tool_console(self):
        root = Path(__file__).resolve().parents[1]
        page = (root / "apps" / "web" / "src" / "workspace" / "AgentComputerPage.tsx").read_text(encoding="utf-8")
        self.assertIn('general: "General agent computer"', page)
        self.assertIn('"computer.python.exec"', page)
        self.assertIn('"computer.terminal.exec"', page)
        self.assertIn("Native tool console", page)
        self.assertIn("/tools/${encodeURIComponent(toolId)}/execute", page)
        self.assertIn("computer_session_id is injected server-side", page)


if __name__ == "__main__":
    unittest.main()
