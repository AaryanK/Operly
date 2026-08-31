import unittest

from packages.kernel.bootstrap import builtin_capabilities
from packages.kernel.policy import CapabilityPolicyEngine
from packages.kernel.registry import CapabilityRegistry
from packages.kernel.schema_validation import SchemaValidationError, validate_schema
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


def personal_context(*permissions: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=None,
        user_id="user-1",
        membership_id=None,
        role="personal",
        permissions=frozenset(permissions),
        channel="web",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        scope_kind=ScopeKind.PERSONAL,
        principal_id="user:user-1",
    )


class KernelContractTests(unittest.TestCase):
    def setUp(self):
        self.registry = CapabilityRegistry(builtin_capabilities())

    def test_generic_registry_filters_personal_primitives_by_permission(self):
        context = personal_context("workspace:read", "tasks:read")
        ids = {spec.id for spec in self.registry.effective(context)}
        self.assertIn("system.runtime.status", ids)
        self.assertIn("tasks.list", ids)
        self.assertNotIn("tasks.create", ids)
        self.assertNotIn("workspace.describe", ids)

    def test_policy_fails_closed_without_write_permission(self):
        context = personal_context("tasks:read")
        decision = CapabilityPolicyEngine().evaluate(context, self.registry.get("tasks.create"))
        self.assertEqual(decision.decision.value, "deny")

    def test_personal_task_primitives_cannot_be_exposed_as_workspace_tools(self):
        for capability_id in ("tasks.list", "tasks.create", "tasks.update_status"):
            self.assertEqual(self.registry.get(capability_id).scopes, frozenset({"personal"}))

    def test_input_schema_rejects_unknown_fields(self):
        spec = self.registry.get("tasks.create")
        with self.assertRaises(SchemaValidationError):
            validate_schema({"title": "x", "secret": "nope"}, spec.input_schema)

    def test_search_does_not_grant_authority(self):
        context = personal_context("workspace:read", "tasks:read")
        discovered = {spec.id for spec in self.registry.search("create task", context=context)}
        effective = {spec.id for spec in self.registry.effective(context)}
        self.assertIn("tasks.create", discovered)
        self.assertNotIn("tasks.create", effective)


if __name__ == "__main__":
    unittest.main()
