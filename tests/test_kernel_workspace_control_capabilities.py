import unittest

from packages.kernel.contracts import CapabilityRisk
from packages.kernel.workspace_control_provider import PROVIDER_ID, workspace_control_capabilities


class WorkspaceControlCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.specs = workspace_control_capabilities()
        self.by_id = {spec.id: spec for spec in self.specs}

    def test_capability_ids_are_unique_and_workspace_scoped(self):
        self.assertEqual(len(self.specs), len(self.by_id))
        self.assertGreaterEqual(len(self.specs), 15)
        for spec in self.specs:
            self.assertEqual(spec.provider_id, PROVIDER_ID)
            self.assertEqual(spec.scopes, frozenset({"workspace"}))
            self.assertEqual(spec.resource_scope, "workspace")
            self.assertTrue(spec.permissions)

    def test_access_changing_operations_require_approval(self):
        gated = {
            "workspace.members.add",
            "workspace.members.role.update",
            "workspace.members.remove",
            "workspace.roles.permissions.set",
            "workspace.invitations.create",
            "workspace.invitations.revoke",
        }
        for capability_id in gated:
            self.assertIn(capability_id, self.by_id)
            self.assertTrue(self.by_id[capability_id].approval_required, capability_id)

    def test_destructive_access_operations_are_not_reversible(self):
        remove_member = self.by_id["workspace.members.remove"]
        self.assertEqual(remove_member.risk, CapabilityRisk.HIGH)
        self.assertFalse(remove_member.reversible)

        revoke_invitation = self.by_id["workspace.invitations.revoke"]
        self.assertTrue(revoke_invitation.approval_required)
        self.assertFalse(revoke_invitation.reversible)

    def test_safe_reads_are_not_approval_gated(self):
        for capability_id in (
            "workspace.summary.read",
            "workspace.activity.list",
            "workspace.members.list",
            "workspace.roles.list",
            "workspace.invitations.list",
            "workspace.inventory.movements.list",
        ):
            spec = self.by_id[capability_id]
            self.assertEqual(spec.risk, CapabilityRisk.READ_ONLY)
            self.assertFalse(spec.approval_required)

    def test_inventory_adjustment_has_bounded_contract(self):
        spec = self.by_id["workspace.inventory.adjust"]
        self.assertEqual(spec.permissions, ("inventory:write",))
        self.assertIn("item_id", spec.input_schema["required"])
        self.assertIn("quantity_change", spec.input_schema["required"])
        self.assertFalse(spec.approval_required)
        self.assertTrue(spec.reversible)
        self.assertIn("inventory.stock.adjusted", spec.emits)


if __name__ == "__main__":
    unittest.main()
