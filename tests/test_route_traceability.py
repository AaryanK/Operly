import unittest
from collections import Counter

from packages.kernel.route_traceability import (
    EXPECTED_AGENT_RUNTIME_ROUTE_COUNT,
    EXPECTED_BASE_ROUTE_COUNT,
    EXPECTED_BASE_ROUTE_DIGEST,
    EXPECTED_ROUTE_COUNT,
    classify_route,
    validate_route_traceability,
)
from scripts.route_inventory import route_inventory, route_inventory_digest


class RouteTraceabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = route_inventory()
        cls.digest = route_inventory_digest(cls.rows)

    def test_exact_mounted_surface_is_pinned_and_fully_classified(self):
        self.assertEqual(len(self.rows), EXPECTED_ROUTE_COUNT)
        errors = validate_route_traceability(self.rows, digest=self.digest)
        self.assertEqual(errors, [], "\n" + "\n".join(errors))

    def test_pre_agent_surface_remains_cryptographically_pinned(self):
        agent_sources = {"personal_agent_runtime_router", "workspace_agent_runtime_router"}
        base_rows = [row for row in self.rows if row["source"] not in agent_sources]
        self.assertEqual(len(base_rows), EXPECTED_BASE_ROUTE_COUNT)
        self.assertEqual(route_inventory_digest(base_rows), EXPECTED_BASE_ROUTE_DIGEST)

    def test_runtime_1_ingress_is_exact_and_not_mislabeled_as_direct_kernel_transport(self):
        agent_sources = {"personal_agent_runtime_router", "workspace_agent_runtime_router"}
        rows = [row for row in self.rows if row["source"] in agent_sources]
        self.assertEqual(len(rows), EXPECTED_AGENT_RUNTIME_ROUTE_COUNT)
        for row in rows:
            with self.subTest(operation=row["operation"]):
                classification = classify_route(row)
                self.assertIsNotNone(classification)
                self.assertEqual(classification.category, "agent_ingress")
                self.assertFalse(classification.kernel_governed)
                self.assertFalse(classification.semantic_event_source)
                self.assertFalse(classification.workflow_trigger_identity)

    def test_classification_covers_every_current_operation_and_exposes_legacy_debt(self):
        classifications = [classify_route(row) for row in self.rows]
        self.assertTrue(all(item is not None for item in classifications))
        categories = Counter(item.category for item in classifications if item is not None)
        self.assertGreater(categories["kernel_governed"], 0)
        self.assertEqual(categories["agent_ingress"], EXPECTED_AGENT_RUNTIME_ROUTE_COUNT)
        self.assertGreater(categories["semantic_event_ingress"], 0)
        self.assertGreater(categories["legacy_direct"], 0)
        self.assertGreater(categories["control_plane"], 0)
        self.assertGreater(categories["read_projection"], 0)

    def test_http_paths_never_become_workflow_trigger_identity(self):
        for row in self.rows:
            with self.subTest(operation=row["operation"]):
                classification = classify_route(row)
                self.assertIsNotNone(classification)
                self.assertFalse(classification.workflow_trigger_identity)

    def test_only_verified_public_webhook_ingress_is_direct_semantic_event_ingress(self):
        event_sources = [
            (row, classify_route(row))
            for row in self.rows
            if classify_route(row) is not None and classify_route(row).semantic_event_source
        ]
        self.assertEqual(len(event_sources), 1)
        row, classification = event_sources[0]
        self.assertEqual(row["operation"], "POST /api/public/webhooks/{endpoint_key}")
        self.assertEqual(classification.category, "semantic_event_ingress")

    def test_canonical_tool_transports_are_kernel_governed(self):
        operations = {
            row["operation"]: classify_route(row)
            for row in self.rows
        }
        for operation in (
            "POST /api/workspace-tools/{capability_id}/execute",
            "POST /api/personal-tools/{capability_id}/execute",
            "POST /api/kernel/personal/execute",
            "POST /api/capability-gateway/{binding_id}/invoke",
            "POST /mcp",
        ):
            with self.subTest(operation=operation):
                self.assertIn(operation, operations)
                self.assertTrue(operations[operation].kernel_governed)
                self.assertEqual(operations[operation].category, "kernel_governed")

    def test_direct_product_mutations_are_not_mislabeled_as_trigger_safe(self):
        operations = {
            row["operation"]: classify_route(row)
            for row in self.rows
        }
        for operation in (
            "POST /api/workspace-os/records/{entity}",
            "PATCH /api/workspace-os/records/{entity}/{record_id}",
            "DELETE /api/workspace-os/records/{entity}/{record_id}",
            "POST /api/workspace-simple/invoices",
            "POST /api/workspace-simple/payments",
            "POST /api/workspace-simple/sales",
        ):
            with self.subTest(operation=operation):
                self.assertIn(operation, operations)
                self.assertEqual(operations[operation].category, "legacy_direct")
                self.assertFalse(operations[operation].kernel_governed)
                self.assertFalse(operations[operation].semantic_event_source)


if __name__ == "__main__":
    unittest.main()
