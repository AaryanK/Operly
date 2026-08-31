import ast
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

    def test_computer_runtime_uses_existing_sandbox_runner_not_api_shell(self):
        root = Path(__file__).resolve().parents[1]
        client = (root / "packages" / "workspace_modules" / "agent_computer" / "sandbox.py").read_text(encoding="utf-8")
        provider = (root / "packages" / "workspace_modules" / "agent_computer" / "native_tools.py").read_text(encoding="utf-8")
        router = (root / "packages" / "workspace_modules" / "agent_computer" / "router.py").read_text(encoding="utf-8")
        self.assertIn("OPERLY_SANDBOX_RUNNER_URL", client)
        self.assertIn("OPERLY_SANDBOX_RUNNER_TOKEN", client)
        self.assertNotIn("OPERLY_AGENT_COMPUTER_RUNNER_URL", client)
        self.assertIn("ComputerRunnerClient", provider)
        self.assertIn("computer.runtime.start", router)
        for source in (client, provider, router):
            self.assertNotIn("create_subprocess", source)
            self.assertNotIn("subprocess.run", source)
            self.assertNotIn("async_playwright", source)

    def test_railway_sandbox_runner_is_the_execution_plane(self):
        root = Path(__file__).resolve().parents[1]
        runner_dir = root / "apps" / "sandbox_runner"
        server = (runner_dir / "server.mjs").read_text(encoding="utf-8")
        helper = (runner_dir / "computer_tool.py").read_text(encoding="utf-8")
        package = (runner_dir / "package.json").read_text(encoding="utf-8")

        self.assertFalse((root / "apps" / "computer_runner").exists())
        self.assertIn('from "railway"', server)
        self.assertIn("Sandbox.create", server)
        self.assertIn("Sandbox.connect", server)
        self.assertIn('service: "operly-sandbox-runner"', server)
        self.assertIn('"railway": "3.10.0"', package)
        self.assertIn("/v1/computer/sessions", server)
        self.assertIn("OPERLY_RUNNER_TOKEN", server)
        self.assertIn("RAILWAY_ENVIRONMENT_ID", server)
        self.assertIn("private_network: false", server)

        ast.parse(helper)
        self.assertIn("def python_exec", helper)
        self.assertIn("def terminal_exec", helper)
        self.assertIn("def browser_tool", helper)
        self.assertIn("def web_fetch", helper)
        self.assertIn("def git_tool", helper)
        self.assertIn("def process_list", helper)
        self.assertIn("safe_path", helper)
        self.assertIn("private/link-local network targets are blocked", helper)

    def test_runner_control_protocol_is_authenticated_and_signed(self):
        root = Path(__file__).resolve().parents[1]
        client = (root / "packages" / "workspace_modules" / "agent_computer" / "sandbox.py").read_text(encoding="utf-8")
        server = (root / "apps" / "sandbox_runner" / "server.mjs").read_text(encoding="utf-8")
        self.assertIn("X-Operly-Signature", client)
        self.assertIn("compare_digest", client)
        self.assertIn("x-operly-signature", server)
        self.assertIn("timingSafeEqual", server)
        self.assertIn("METHOD", (root / "apps" / "sandbox_runner" / "README.md").read_text(encoding="utf-8"))

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
