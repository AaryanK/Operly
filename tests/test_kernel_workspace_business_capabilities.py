import unittest

from packages.kernel.contracts import CapabilityRisk
from packages.kernel.workspace_business_provider import PROVIDER_ID, workspace_business_capabilities


class WorkspaceBusinessCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.specs = workspace_business_capabilities()
        self.by_id = {spec.id: spec for spec in self.specs}

    def test_business_capabilities_are_unique_workspace_tools(self):
        self.assertEqual(len(self.specs), len(self.by_id))
        self.assertGreaterEqual(len(self.specs), 6)
        for spec in self.specs:
            self.assertEqual(spec.provider_id, PROVIDER_ID)
            self.assertEqual(spec.scopes, frozenset({"workspace"}))
            self.assertEqual(spec.resource_scope, "workspace")
            self.assertTrue(spec.permissions)

    def test_context_and_discovery_tools_are_read_only(self):
        for capability_id in (
            "workspace.search",
            "workspace.attention.list",
            "workspace.customer.snapshot",
        ):
            spec = self.by_id[capability_id]
            self.assertEqual(spec.risk, CapabilityRisk.READ_ONLY)
            self.assertFalse(spec.approval_required)

    def test_atomic_business_mutations_are_approval_gated(self):
        for capability_id in (
            "workspace.sales.complete",
            "workspace.finance.invoice.create_simple",
            "workspace.finance.payment.record",
        ):
            spec = self.by_id[capability_id]
            self.assertEqual(spec.risk, CapabilityRisk.MEDIUM)
            self.assertTrue(spec.approval_required)
            self.assertFalse(spec.reversible)

    def test_sale_contract_is_business_outcome_not_raw_rows(self):
        spec = self.by_id["workspace.sales.complete"]
        self.assertEqual(spec.permissions, ("orders:write",))
        self.assertIn("items", spec.input_schema["required"])
        self.assertIn("sale.completed", spec.emits)
        properties = spec.output_schema["properties"]
        self.assertIn("order_id", properties)
        self.assertIn("invoice_id", properties)
        self.assertIn("payment_id", properties)

    def test_finance_workflows_require_finance_write(self):
        invoice = self.by_id["workspace.finance.invoice.create_simple"]
        payment = self.by_id["workspace.finance.payment.record"]
        self.assertEqual(invoice.permissions, ("finance:write",))
        self.assertEqual(payment.permissions, ("finance:write",))
        self.assertIn("invoice.created", invoice.emits)
        self.assertIn("payment.recorded", payment.emits)


if __name__ == "__main__":
    unittest.main()
