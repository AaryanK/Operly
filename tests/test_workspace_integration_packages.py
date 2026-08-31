import unittest
from pathlib import Path

from packages.kernel.contracts import CapabilityRisk
from packages.workspace_modules.integrations.canva import (
    CANVA_SCOPE_BY_CAPABILITY,
    workspace_canva_capabilities,
)
from packages.workspace_modules.integrations.canva.authoring import (
    SCOPE_BY_CAPABILITY,
    workspace_canva_authoring_capabilities,
)
from packages.workspace_modules.integrations.discord import workspace_discord_capabilities
from packages.workspace_modules.integrations.google import workspace_google_capabilities


class WorkspaceIntegrationPackageTests(unittest.TestCase):
    def test_each_provider_surface_is_workspace_scoped_and_unique(self):
        groups = (
            workspace_google_capabilities(),
            workspace_canva_capabilities(),
            workspace_canva_authoring_capabilities(),
            workspace_discord_capabilities(),
        )
        ids: set[str] = set()
        for group in groups:
            for spec in group:
                self.assertEqual(spec.scopes, frozenset({"workspace"}))
                self.assertEqual(spec.resource_scope, "workspace")
                self.assertNotIn(spec.id, ids)
                ids.add(spec.id)

    def test_canva_requires_exact_external_scopes(self):
        self.assertEqual(
            CANVA_SCOPE_BY_CAPABILITY["canva.designs.list"],
            frozenset({"design:meta:read"}),
        )
        self.assertEqual(
            CANVA_SCOPE_BY_CAPABILITY["canva.design.create"],
            frozenset({"design:content:write"}),
        )
        self.assertEqual(
            CANVA_SCOPE_BY_CAPABILITY["canva.design.export.create"],
            frozenset({"design:content:read"}),
        )
        self.assertEqual(
            CANVA_SCOPE_BY_CAPABILITY["canva.folder.items.list"],
            frozenset({"folder:read"}),
        )
        self.assertEqual(
            SCOPE_BY_CAPABILITY["canva.design.dataset"],
            frozenset({"design:content:read"}),
        )
        self.assertEqual(
            SCOPE_BY_CAPABILITY["canva.brand_templates.list"],
            frozenset({"brandtemplate:meta:read"}),
        )
        self.assertEqual(
            SCOPE_BY_CAPABILITY["canva.brand_template.dataset"],
            frozenset({"brandtemplate:content:read"}),
        )
        self.assertEqual(
            SCOPE_BY_CAPABILITY["canva.autofill.create"],
            frozenset({"design:content:write"}),
        )

    def test_external_content_mutations_are_approval_gated(self):
        canva = {spec.id: spec for spec in workspace_canva_capabilities()}
        canva_authoring = {
            spec.id: spec for spec in workspace_canva_authoring_capabilities()
        }
        discord = {spec.id: spec for spec in workspace_discord_capabilities()}
        self.assertEqual(canva["canva.design.create"].risk, CapabilityRisk.MEDIUM)
        self.assertTrue(canva["canva.design.create"].approval_required)
        self.assertEqual(
            canva_authoring["canva.autofill.create"].risk,
            CapabilityRisk.HIGH,
        )
        self.assertTrue(canva_authoring["canva.autofill.create"].approval_required)
        self.assertFalse(canva_authoring["canva.autofill.create"].reversible)
        for capability_id in (
            "discord.message.send",
            "discord.reaction.add",
            "discord.thread.create",
        ):
            self.assertTrue(discord[capability_id].approval_required, capability_id)

    def test_canva_authoring_has_only_stable_dataset_and_autofill_boundary(self):
        capabilities = {spec.id for spec in workspace_canva_authoring_capabilities()}
        self.assertIn("canva.design.dataset", capabilities)
        self.assertIn("canva.brand_templates.list", capabilities)
        self.assertIn("canva.brand_template.dataset", capabilities)
        self.assertIn("canva.autofill.create", capabilities)
        self.assertIn("canva.autofill.get", capabilities)
        self.assertNotIn("canva.design.pages.list", capabilities)
        self.assertNotIn("canva.design.pages.list", SCOPE_BY_CAPABILITY)

    def test_discord_runtime_has_no_ai_execution_path(self):
        root = Path(__file__).resolve().parents[1]
        source = (
            root
            / "packages"
            / "workspace_modules"
            / "integrations"
            / "discord"
            / "bot.py"
        ).read_text(encoding="utf-8")
        lifecycle = (
            root
            / "packages"
            / "workspace_modules"
            / "integrations"
            / "discord"
            / "lifecycle.py"
        ).read_text(encoding="utf-8")
        for token in (
            "AgentRuntime",
            "ChannelService.handle",
            "model_runtime",
            "secure_runtime",
            "packages.agents",
        ):
            self.assertNotIn(token, source)
            self.assertNotIn(token, lifecycle)
        self.assertIn("AI chat is not enabled yet", source)

    def test_provider_write_permissions_are_operly_permissions(self):
        canva = {spec.id: spec for spec in workspace_canva_capabilities()}
        canva_authoring = {
            spec.id: spec for spec in workspace_canva_authoring_capabilities()
        }
        discord = {spec.id: spec for spec in workspace_discord_capabilities()}
        self.assertEqual(
            canva["canva.design.create"].permissions,
            ("marketing:write",),
        )
        self.assertEqual(
            canva_authoring["canva.autofill.create"].permissions,
            ("marketing:write",),
        )
        self.assertEqual(
            discord["discord.message.send"].permissions,
            ("discord:write",),
        )


if __name__ == "__main__":
    unittest.main()
