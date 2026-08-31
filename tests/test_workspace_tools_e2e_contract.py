import unittest
from pathlib import Path

from packages.kernel.bootstrap import builtin_capabilities
from packages.workspace_modules.tools import workspace_capabilities
from packages.workspace_modules.tools.router import workspace_tool_endpoint


class WorkspaceToolsE2EContractTests(unittest.TestCase):
    def test_workspace_owned_tools_live_in_workspace_modules_package(self):
        root = Path(__file__).resolve().parents[1]
        for legacy in (
            "workspace_os_provider.py",
            "workspace_control_provider.py",
            "workspace_business_provider.py",
            "workspace_google_provider.py",
            "provider_availability.py",
        ):
            self.assertFalse((root / "packages" / "kernel" / legacy).exists(), legacy)
        for current in (
            "records.py", "controls.py", "business.py", "google.py", "availability.py",
            "system.py", "runtime.py", "router.py",
        ):
            self.assertTrue((root / "packages" / "workspace_modules" / "tools" / current).exists(), current)
        self.assertFalse((root / "apps" / "api" / "workspace_tools_router.py").exists())

    def test_generic_builtins_do_not_own_workspace_business_tools(self):
        ids = {spec.id for spec in builtin_capabilities()}
        self.assertNotIn("workspace.describe", ids)
        self.assertNotIn("workspace.modules.list", ids)
        for spec in builtin_capabilities():
            if spec.id.startswith("tasks."):
                self.assertEqual(spec.scopes, frozenset({"personal"}))

    def test_every_workspace_tool_has_a_stable_http_execute_endpoint(self):
        specs = workspace_capabilities()
        endpoints = [workspace_tool_endpoint(spec.id) for spec in specs]
        self.assertEqual(len(endpoints), len(set(endpoints)))
        self.assertTrue(all(endpoint.startswith("/workspace-tools/") for endpoint in endpoints))
        self.assertTrue(all(endpoint.endswith("/execute") for endpoint in endpoints))
        self.assertGreater(len(endpoints), 100)

    def test_workspace_system_tools_are_owned_by_workspace_package(self):
        ids = {spec.id for spec in workspace_capabilities()}
        self.assertIn("workspace.describe", ids)
        self.assertIn("workspace.modules.list", ids)
        self.assertIn("workspace.search", ids)
        self.assertIn("google.gmail.search", ids)


if __name__ == "__main__":
    unittest.main()
