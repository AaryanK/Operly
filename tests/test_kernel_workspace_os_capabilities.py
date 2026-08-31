import unittest

from packages.workspace_modules.tools.records import capability_id, workspace_record_capabilities
from packages.workspace_modules.tools.runtime import build_workspace_runtime


class WorkspaceOSCapabilityInfrastructureTests(unittest.TestCase):
    def test_workspace_os_generates_broad_deterministic_capability_surface(self):
        specs = workspace_record_capabilities()
        ids = {spec.id for spec in specs}
        self.assertEqual(len(ids), len(specs))
        self.assertGreater(len(specs), 100)
        self.assertIn(capability_id("crm", "contacts", "list"), ids)
        self.assertIn(capability_id("crm", "contacts", "create"), ids)
        self.assertIn(capability_id("finance", "invoices", "update"), ids)
        self.assertIn(capability_id("research", "datasets", "list"), ids)
        self.assertIn(capability_id("projects", "projects", "delete"), ids)

    def test_generated_permissions_follow_workspace_record_registry(self):
        by_id = {spec.id: spec for spec in workspace_record_capabilities()}
        contacts_list = by_id[capability_id("crm", "contacts", "list")]
        contacts_create = by_id[capability_id("crm", "contacts", "create")]
        invoice_list = by_id[capability_id("finance", "invoices", "list")]
        self.assertEqual(contacts_list.permissions, ("crm:read",))
        self.assertEqual(contacts_create.permissions, ("crm:write",))
        self.assertEqual(invoice_list.permissions, ("finance:read",))

    def test_delete_is_infrastructure_gated_for_explicit_approval(self):
        by_id = {spec.id: spec for spec in workspace_record_capabilities()}
        delete_contact = by_id[capability_id("crm", "contacts", "delete")]
        self.assertTrue(delete_contact.approval_required)
        self.assertFalse(delete_contact.reversible)
        self.assertEqual(delete_contact.risk.value, "medium")

    def test_generated_write_contract_exposes_real_workspace_fields(self):
        by_id = {spec.id: spec for spec in workspace_record_capabilities()}
        create_contact = by_id[capability_id("crm", "contacts", "create")]
        properties = create_contact.input_schema["properties"]
        self.assertIn("name", properties)
        self.assertIn("email", properties)
        self.assertIn("phone", properties)
        self.assertIn("name", create_contact.input_schema["required"])
        self.assertFalse(create_contact.input_schema["additionalProperties"])

    def test_workspace_composition_registers_workspace_module_tool_surface(self):
        runtime = build_workspace_runtime()
        ids = {spec.id for spec in runtime.registry.all()}
        self.assertIn(capability_id("crm", "contacts", "list"), ids)
        self.assertIn(capability_id("finance", "payments", "create"), ids)
        self.assertIn(capability_id("research", "experiments", "update"), ids)


if __name__ == "__main__":
    unittest.main()
