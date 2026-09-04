import unittest
from collections import Counter

from packages.kernel.route_traceability import (
    EXPECTED_ROUTE_COUNT,
    EXPECTED_ROUTE_DIGEST,
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
        self.assertEqual(self.digest, EXPECTED_ROUTE_DIGEST)
        errors = validate_route_traceability(self.rows, digest=self.digest)
        self.assertEqual(errors, [], "\n" + "\n".join(errors))

    def test_classification_covers_every_current_operation_and_exposes_legacy_debt(self):
        classifications = [classify_route(row) for row in self.rows]
        self.assertTrue(all(item is not None for item in classifications))
        categories = Counter(item.category for item in classifications if item is not None)
        self.assertGreater(categories["kernel_governed"], 0)
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
