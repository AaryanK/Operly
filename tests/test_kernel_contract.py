import unittest
from dataclasses import replace

from packages.kernel.bootstrap import builtin_capabilities
from packages.kernel.policy import CapabilityPolicyEngine
from packages.kernel.registry import CapabilityRegistry
from packages.kernel.schema_validation import SchemaValidationError, validate_schema
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


def workspace_context(*permissions: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        membership_id="member-1",
        role="employee",
        permissions=frozenset(permissions),
        channel="web",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        scope_kind=ScopeKind.WORKSPACE,
        principal_id="user:user-1",
    )


class KernelContractTests(unittest.TestCase):
    def setUp(self):
        self.registry = CapabilityRegistry(builtin_capabilities())

    def test_registry_filters_effective_capabilities_by_permission(self):
        context = workspace_context("workspace:read", "tasks:read")
        ids = {spec.id for spec in self.registry.effective(context)}
        self.assertIn("workspace.describe", ids)
        self.assertIn("tasks.list", ids)
        self.assertNotIn("tasks.create", ids)

    def test_policy_fails_closed_without_write_permission(self):
        context = workspace_context("tasks:read")
        decision = CapabilityPolicyEngine().evaluate(context, self.registry.get("tasks.create"))
        self.assertEqual(decision.decision.value, "deny")

    def test_guest_ceiling_is_respected_through_effective_permissions(self):
        context = replace(
            workspace_context("workspace:read", "tasks:read"),
            membership_id=None,
            role="guest",
            workspace_mode="guest",
        )
        ids = {spec.id for spec in self.registry.effective(context)}
        self.assertIn("tasks.list", ids)
        self.assertNotIn("tasks.create", ids)

    def test_input_schema_rejects_unknown_fields(self):
        spec = self.registry.get("tasks.create")
        with self.assertRaises(SchemaValidationError):
            validate_schema({"title": "x", "secret": "nope"}, spec.input_schema)

    def test_search_does_not_grant_authority(self):
        context = workspace_context("workspace:read", "tasks:read")
        discovered = {spec.id for spec in self.registry.search("create task", context=context)}
        effective = {spec.id for spec in self.registry.effective(context)}
        self.assertIn("tasks.create", discovered)
        self.assertNotIn("tasks.create", effective)


if __name__ == "__main__":
    unittest.main()
