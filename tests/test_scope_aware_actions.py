import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.actions.service import ActionService, ActionStatus
from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.registry import CapabilityRegistry
from packages.database.company_models import BusinessActionRecord
from packages.database.db import Base
from packages.database.models import AppUser, Approval, Tenant
from packages.database.schema import import_all_models
from packages.security.execution_context import ScopeKind


class FakeProvider:
    name = "scope-test"
    capabilities = (
        CapabilityDefinition(
            "scope_test.write",
            "scope_test_write",
            "Write a test value through the governed action lifecycle.",
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("scope:test:write",),
            approval_policy=ApprovalPolicy.ALWAYS,
        ),
    )

    def supports(self, capability_name: str) -> bool:
        return capability_name in {"scope_test.write", "scope_test_write"}

    async def execute(self, context, capability_name, arguments):
        return CapabilityResult(
            True,
            True,
            {
                "scope_id": context.tenant_id,
                "actor_id": context.actor_id,
                "value": arguments["value"],
            },
        )

    async def verify(self, context, capability_name, arguments, result):
        return CapabilityResult(
            result.success and result.evidence.get("value") == arguments["value"],
            result.changed,
            dict(result.evidence),
        )


class ScopeAwareActionTests(unittest.IsolatedAsyncioTestCase):
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
            user = AppUser(email="action-owner@example.test", display_name="Owner", active=True)
            other = AppUser(email="other-action@example.test", display_name="Other", active=True)
            workspace = Tenant(name="ANHITRA", slug="anhitra")
            db.add_all([user, other, workspace])
            await db.commit()
            self.user_id = user.id
            self.other_user_id = other.id
            self.workspace_id = workspace.id

        self.resolved_scope_ids = []
        self.registry = CapabilityRegistry(
            enabled_resolver=lambda scope_id, definition: self.resolved_scope_ids.append(scope_id) or True,
        )
        self.registry.register(FakeProvider())

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _propose_workspace(self, db, *, idempotency_key=None):
        service = ActionService(
            db,
            self.registry,
            authority={"scope:test:write"},
            actor_id=self.user_id,
        )
        return await service.propose(
            tenant_id=self.workspace_id,
            objective="workspace write",
            capability="scope_test.write",
            arguments={"value": "workspace"},
            rationale="test workspace action",
            expected_outcome="workspace value persisted",
            risk_level="medium",
            idempotency_key=idempotency_key,
        )

    async def _propose_personal(self, db, *, idempotency_key=None):
        service = ActionService(
            db,
            self.registry,
            authority={"scope:test:write"},
            actor_id=self.user_id,
        )
        return await service.propose(
            tenant_id=None,
            owner_user_id=self.user_id,
            scope_kind=ScopeKind.PERSONAL,
            objective="personal write",
            capability="scope_test.write",
            arguments={"value": "personal"},
            rationale="test personal action",
            expected_outcome="personal value persisted",
            risk_level="medium",
            idempotency_key=idempotency_key,
        )

    async def test_workspace_action_and_approval_keep_legacy_owner_boundary(self):
        async with self.sessions() as db:
            action = await self._propose_workspace(db)
            approval = await db.get(Approval, action.approval_id)

        self.assertEqual(action.scope_kind, ScopeKind.WORKSPACE.value)
        self.assertEqual(action.tenant_id, self.workspace_id)
        self.assertIsNone(action.owner_user_id)
        self.assertEqual(action.status, ActionStatus.WAITING_APPROVAL)
        self.assertIsNotNone(approval)
        self.assertEqual(approval.scope_kind, ScopeKind.WORKSPACE.value)
        self.assertEqual(approval.tenant_id, self.workspace_id)
        self.assertIsNone(approval.owner_user_id)
        self.assertIn(self.workspace_id, self.resolved_scope_ids)

    async def test_personal_action_and_approval_are_owned_by_user_not_workspace(self):
        async with self.sessions() as db:
            action = await self._propose_personal(db)
            approval = await db.get(Approval, action.approval_id)

        self.assertEqual(action.scope_kind, ScopeKind.PERSONAL.value)
        self.assertIsNone(action.tenant_id)
        self.assertEqual(action.owner_user_id, self.user_id)
        self.assertEqual(action.status, ActionStatus.WAITING_APPROVAL)
        self.assertIsNotNone(approval)
        self.assertEqual(approval.scope_kind, ScopeKind.PERSONAL.value)
        self.assertIsNone(approval.tenant_id)
        self.assertEqual(approval.owner_user_id, self.user_id)
        self.assertIn(f"personal:{self.user_id}", self.resolved_scope_ids)

    async def test_same_idempotency_key_is_isolated_between_personal_and_workspace(self):
        async with self.sessions() as db:
            workspace_action = await self._propose_workspace(db, idempotency_key="same-call")
            personal_action = await self._propose_personal(db, idempotency_key="same-call")
            rows = (
                await db.scalars(
                    select(BusinessActionRecord).where(
                        BusinessActionRecord.idempotency_key == "same-call"
                    )
                )
            ).all()

        self.assertNotEqual(workspace_action.id, personal_action.id)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row.scope_kind for row in rows}, {"workspace", "personal"})

    async def test_personal_approval_is_owner_only_and_executes_in_personal_namespace(self):
        async with self.sessions() as db:
            action = await self._propose_personal(db)

            wrong_actor = ActionService(
                db,
                self.registry,
                authority={"scope:test:write"},
                actor_id=self.other_user_id,
            )
            with self.assertRaisesRegex(PermissionError, "Only the Personal action owner"):
                await wrong_actor.approve_personal(self.user_id, action.id)

            owner = ActionService(
                db,
                self.registry,
                authority={"scope:test:write"},
                actor_id=self.user_id,
            )
            approved = await owner.approve_personal(self.user_id, action.id)
            approval = await db.get(Approval, action.approval_id)

        self.assertEqual(approved.status, ActionStatus.VERIFIED)
        self.assertEqual(approval.status, "approved")
        self.assertIsNone(approved.tenant_id)
        self.assertEqual(approved.owner_user_id, self.user_id)
        self.assertIn(f"personal:{self.user_id}", self.resolved_scope_ids)

    async def test_personal_proposal_rejects_actor_owner_mismatch(self):
        async with self.sessions() as db:
            service = ActionService(
                db,
                self.registry,
                authority={"scope:test:write"},
                actor_id=self.other_user_id,
            )
            with self.assertRaisesRegex(PermissionError, "owner must match"):
                await service.propose(
                    tenant_id=None,
                    owner_user_id=self.user_id,
                    scope_kind=ScopeKind.PERSONAL,
                    objective="wrong owner",
                    capability="scope_test.write",
                    arguments={"value": "blocked"},
                    rationale="must fail",
                    expected_outcome="none",
                    risk_level="medium",
                )


if __name__ == "__main__":
    unittest.main()
