import unittest

from packages.database.schema import ALEMBIC_HEAD
from packages.kernel.approvals import arguments_hash
from packages.kernel.contracts import RuntimeRequest
from packages.kernel.idempotency import _arguments_hash


class KernelInfrastructureContractTests(unittest.TestCase):
    def test_idempotency_hash_is_canonical_for_argument_order(self):
        left = RuntimeRequest(
            goal="create contact",
            capability_id="workspace_os.crm.contacts.create",
            arguments={"name": "A", "email": "a@example.com"},
            request_id="req-1",
        )
        right = RuntimeRequest(
            goal="create contact",
            capability_id="workspace_os.crm.contacts.create",
            arguments={"email": "a@example.com", "name": "A"},
            request_id="req-1",
        )
        self.assertEqual(_arguments_hash(left), _arguments_hash(right))

    def test_idempotency_hash_binds_goal_capability_and_arguments(self):
        base = RuntimeRequest(
            goal="create contact",
            capability_id="workspace_os.crm.contacts.create",
            arguments={"name": "A"},
        )
        changed_goal = RuntimeRequest(
            goal="create another contact",
            capability_id=base.capability_id,
            arguments=base.arguments,
        )
        changed_capability = RuntimeRequest(
            goal=base.goal,
            capability_id="workspace_os.crm.organizations.create",
            arguments=base.arguments,
        )
        changed_arguments = RuntimeRequest(
            goal=base.goal,
            capability_id=base.capability_id,
            arguments={"name": "B"},
        )
        self.assertNotEqual(_arguments_hash(base), _arguments_hash(changed_goal))
        self.assertNotEqual(_arguments_hash(base), _arguments_hash(changed_capability))
        self.assertNotEqual(_arguments_hash(base), _arguments_hash(changed_arguments))

    def test_approval_hash_binds_exact_capability_and_arguments(self):
        first = arguments_hash("workspace_os.crm.contacts.delete", {"record_id": "1"})
        same = arguments_hash("workspace_os.crm.contacts.delete", {"record_id": "1"})
        other = arguments_hash("workspace_os.crm.contacts.delete", {"record_id": "2"})
        self.assertEqual(first, same)
        self.assertNotEqual(first, other)

    def test_runtime_request_carries_approval_without_changing_capability_arguments(self):
        request = RuntimeRequest(
            capability_id="workspace_os.crm.contacts.delete",
            arguments={"record_id": "1"},
            approval_id="approval-1",
        )
        self.assertEqual(request.approval_id, "approval-1")
        self.assertEqual(dict(request.arguments), {"record_id": "1"})

    def test_schema_head_contains_infrastructure_migrations(self):
        self.assertEqual(ALEMBIC_HEAD, "0049_kernel_approvals")


if __name__ == "__main__":
    unittest.main()
