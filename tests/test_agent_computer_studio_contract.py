import unittest
from pathlib import Path

from packages.kernel.contracts import CapabilityRisk
from packages.workspace_modules.studio import workspace_studio_capabilities


class AgentComputerStudioContractTests(unittest.TestCase):
    def test_studio_surface_is_workspace_scoped_and_permissioned(self):
        specs = {spec.id: spec for spec in workspace_studio_capabilities()}
        expected = {
            "studio.projects.list",
            "studio.project.inspect",
            "studio.solution.status",
            "studio.solution.deploy",
            "studio.solution.rollback",
            "studio.solution.domain.request",
        }
        self.assertEqual(set(specs), expected)
        for spec in specs.values():
            self.assertEqual(spec.scopes, frozenset({"workspace"}))
            self.assertEqual(spec.resource_scope, "workspace")
        self.assertEqual(specs["studio.projects.list"].permissions, ("solution:read",))
        self.assertEqual(specs["studio.solution.deploy"].permissions, ("solution:write",))

    def test_deploy_and_rollback_are_high_risk_exact_approval_operations(self):
        specs = {spec.id: spec for spec in workspace_studio_capabilities()}
        for capability_id in ("studio.solution.deploy", "studio.solution.rollback"):
            spec = specs[capability_id]
            self.assertEqual(spec.risk, CapabilityRisk.HIGH)
            self.assertTrue(spec.approval_required)
            self.assertTrue(spec.reversible)
        self.assertEqual(specs["studio.solution.domain.request"].risk, CapabilityRisk.MEDIUM)
        self.assertTrue(specs["studio.solution.domain.request"].approval_required)

    def test_agent_computer_is_an_interface_not_a_privileged_executor(self):
        root = Path(__file__).resolve().parents[1]
        router = (root / "packages" / "workspace_modules" / "agent_computer" / "router.py").read_text(encoding="utf-8")
        studio = (root / "packages" / "workspace_modules" / "studio" / "provider.py").read_text(encoding="utf-8")
        self.assertIn('context.can("computer:execute")', router)
        self.assertIn("build_workspace_runtime", router)
        self.assertIn("RuntimeRequest", router)
        self.assertIn('"deploy": "studio.solution.deploy"', router)
        self.assertIn('"rollback": "studio.solution.rollback"', router)
        self.assertIn('"domain": "studio.solution.domain.request"', router)
        for forbidden in (
            "business_brain",
            "model_runtime",
            "AgentRuntime",
            "packages.agents",
            "subprocess",
            "playwright",
            "selenium",
        ):
            self.assertNotIn(forbidden, router)
            self.assertNotIn(forbidden, studio)

    def test_static_deployer_is_fail_closed_for_unbuilt_application_source(self):
        root = Path(__file__).resolve().parents[1]
        studio = (root / "packages" / "workspace_modules" / "studio" / "provider.py").read_text(encoding="utf-8")
        self.assertIn('"dist/index.html"', studio)
        self.assertIn('"build/index.html"', studio)
        self.assertIn('"package.json"', studio)
        self.assertIn("has no committed dist/build output", studio)
        self.assertIn("MAX_PUBLISH_FILES", studio)
        self.assertIn("MAX_PUBLISH_BYTES", studio)
        self.assertIn("OPERLY_DEPLOYMENT_ROOT", studio)

    def test_computer_session_persists_exact_request_and_approval_handles(self):
        root = Path(__file__).resolve().parents[1]
        models = (root / "packages" / "database" / "agent_computer_models.py").read_text(encoding="utf-8")
        router = (root / "packages" / "workspace_modules" / "agent_computer" / "router.py").read_text(encoding="utf-8")
        for field in ("current_capability_id", "current_request_id", "current_run_id", "approval_id"):
            self.assertIn(field, models)
        self.assertIn("approval_id=approval_id", router)
        self.assertIn("request_id=request_id", router)
        self.assertIn("waiting_for_approval", router)


if __name__ == "__main__":
    unittest.main()
