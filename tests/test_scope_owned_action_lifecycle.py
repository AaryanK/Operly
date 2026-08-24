import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.actions.service import ActionService, ActionStatus
from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.capabilities.registry import CapabilityRegistry
from packages.database.company_models import BusinessEventRecord
from packages.database.db import Base
from packages.database.models import AppUser, Approval, Tenant
from packages.database.schema import import_all_models


class _ScopeWriteProvider(BaseProvider):
    name = "scope_write_test"
    capabilities = (
        CapabilityDefinition(
            "test.scope_write",
            "test_scope_write",
            "Write a test value through the governed action lifecycle.",
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "scope_kind": {"type": "string"},
                    "owner_user_id": {},
                    "tenant_id": {},
                },
                "required": ["ok", "scope_kind", "owner_user_id", "tenant_id"],
                "additionalProperties": False,
            },
            risk_level="medium",
            permissions=("tasks:write",),
            approval_policy=ApprovalPolicy.ALWAYS,
        ),
    )

    async def execute(self, context, capability_name, arguments):
        del capability_name, arguments
        return CapabilityResult(
            True,
            True,
            {
                "ok": True,
                "scope_kind": context.scope_kind,
                "owner_user_id": context.owner_user_id,
                "tenant_id": context.tenant_id,
            },
        )

    async def verify(self, context, capability_name, arguments, result):
        del context, capability_name, arguments
        return CapabilityResult(result.success, result.changed, dict(result.evidence))


class ScopeOwnedActionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with self.sessions() as db:
            user = AppUser(email="owner@example.test", display_name="Owner", active=True)
            other = AppUser(email="other@example.test", display_name="Other", active=True)
            tenant = Tenant(name="ANHITRA", slug="anhitra")
            db.add_all([user, other, tenant])
            await db.commit()
            self.user_id = user.id
            self.other_user_id = other.id
            self.tenant_id = tenant.id

        self.registry = CapabilityRegistry()
        self.registry.register(_ScopeWriteProvider())

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_personal_action_and_approval_never_acquire_workspace_owner(self):
        async with self.sessions() as db:
            service = ActionService(
                db,
                self.registry,
                authority={"tasks:write"},
                actor_id=self.user_id,
            )
            action = await service.propose(
                tenant_id=None,
                owner_user_id=self.user_id,
                objective="personal write",
                capability="test.scope_write",
                arguments={"value": "personal"},
                rationale="test",
                expected_outcome="test",
                risk_level="medium",
                idempotency_key="personal-write-1",
            )
            await db.commit()

            self.assertEqual(action.scope_kind, "personal")
            self.assertIsNone(action.tenant_id)
            self.assertEqual(action.owner_user_id, self.user_id)
            self.assertEqual(action.status, ActionStatus.WAITING_APPROVAL)

            approval = await db.get(Approval, action.approval_id)
            self.assertEqual(approval.scope_kind, "personal")
            self.assertIsNone(approval.tenant_id)
            self.assertEqual(approval.owner_user_id, self.user_id)

            events = (
                await db.scalars(
                    select(BusinessEventRecord).where(
                        BusinessEventRecord.owner_user_id == self.user_id
                    )
                )
            ).all()
            self.assertGreaterEqual(len(events), 2)
            self.assertTrue(all(row.scope_kind == "personal" for row in events))
            self.assertTrue(all(row.tenant_id is None for row in events))

    async def test_personal_approval_is_owner_bound_and_executes_in_personal_context(self):
        async with self.sessions() as db:
            service = ActionService(
                db,
                self.registry,
                authority={"tasks:write"},
                actor_id=self.user_id,
            )
            action = await service.propose(
                tenant_id=None,
                owner_user_id=self.user_id,
                objective="personal write",
                capability="test.scope_write",
                arguments={"value": "personal"},
                rationale="test",
                expected_outcome="test",
                risk_level="medium",
            )
            await db.flush()

            with self.assertRaisesRegex(PermissionError, "authenticated actor"):
                await service.approve_personal(self.other_user_id, action.id)

            approved = await service.approve_personal(self.user_id, action.id)
            await db.commit()
            self.assertEqual(approved.status, ActionStatus.VERIFIED)
            self.assertIn('"scope_kind": "personal"', approved.result_json)
            self.assertIn(f'"owner_user_id": "{self.user_id}"', approved.result_json)
            self.assertIn('"tenant_id": null', approved.result_json)
            approval = await db.get(Approval, approved.approval_id)
            self.assertEqual(approval.status, "approved")

    async def test_personal_service_cannot_impersonate_another_owner(self):
        async with self.sessions() as db:
            other_service = ActionService(
                db,
                self.registry,
                authority={"tasks:write"},
                actor_id=self.other_user_id,
            )
            with self.assertRaisesRegex(PermissionError, "authenticated actor"):
                await other_service.propose(
                    tenant_id=None,
                    owner_user_id=self.user_id,
                    objective="forged personal write",
                    capability="test.scope_write",
                    arguments={"value": "x"},
                    rationale="test",
                    expected_outcome="test",
                    risk_level="medium",
                )

    async def test_workspace_action_preserves_existing_tenant_contract(self):
        async with self.sessions() as db:
            service = ActionService(
                db,
                self.registry,
                authority={"tasks:write"},
                actor_id=self.user_id,
            )
            action = await service.propose(
                tenant_id=self.tenant_id,
                objective="workspace write",
                capability="test.scope_write",
                arguments={"value": "workspace"},
                rationale="test",
                expected_outcome="test",
                risk_level="medium",
            )
            await db.commit()

            self.assertEqual(action.scope_kind, "workspace")
            self.assertEqual(action.tenant_id, self.tenant_id)
            self.assertIsNone(action.owner_user_id)
            approval = await db.get(Approval, action.approval_id)
            self.assertEqual(approval.scope_kind, "workspace")
            self.assertEqual(approval.tenant_id, self.tenant_id)
            self.assertIsNone(approval.owner_user_id)

    async def test_service_rejects_ambiguous_or_ownerless_scope(self):
        async with self.sessions() as db:
            service = ActionService(
                db,
                self.registry,
                authority={"tasks:write"},
                actor_id=self.user_id,
            )
            with self.assertRaisesRegex(ValueError, "exactly one"):
                await service.propose(
                    tenant_id=None,
                    owner_user_id=None,
                    objective="invalid",
                    capability="test.scope_write",
                    arguments={"value": "x"},
                    rationale="test",
                    expected_outcome="test",
                    risk_level="medium",
                )
            with self.assertRaisesRegex(ValueError, "exactly one"):
                await service.propose(
                    tenant_id=self.tenant_id,
                    owner_user_id=self.user_id,
                    objective="invalid",
                    capability="test.scope_write",
                    arguments={"value": "x"},
                    rationale="test",
                    expected_outcome="test",
                    risk_level="medium",
                )


if __name__ == "__main__":
    unittest.main()
