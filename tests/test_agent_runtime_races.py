from __future__ import annotations

from datetime import datetime, timedelta
import unittest

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.agent_runtime import (
    AgentPlan,
    AgentPlanStep,
    AgentRunStatus,
    AgentRuntimeSettings,
    GovernedAgentRuntime,
)
from packages.agent_runtime.orchestrator import DurableAgentOrchestrator
from packages.agent_runtime.store import (
    claim_run,
    create_run,
    renew_run_lease,
    request_cancellation,
)
from packages.database.agent_runtime_models import AgentRuntimeRun
from packages.database.db import Base
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models
from packages.kernel.contracts import (
    AuthorizationDecision,
    CapabilityRisk,
    CapabilitySpec,
    RuntimeResponse,
)
from packages.kernel.registry import CapabilityRegistry
from packages.security.execution_context import resolve_execution_context
from packages.security.surfaces import SurfaceKind


def read_capability() -> CapabilitySpec:
    return CapabilitySpec(
        id="records.read",
        version="1",
        display_name="records.read",
        description="test read",
        provider_id="fake",
        scopes=frozenset({"workspace"}),
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        risk=CapabilityRisk.READ_ONLY,
        approval_required=False,
    )


class FakeKernel:
    def __init__(self) -> None:
        self.registry = CapabilityRegistry()
        self.registry.register(read_capability())
        self.calls = []

    async def execute(self, db, *, context, request):
        del db
        self.calls.append((context, request))
        return RuntimeResponse(
            run_id="33333333-3333-3333-3333-333333333333",
            status="completed",
            capability_id=request.capability_id,
            decision=AuthorizationDecision.ALLOW,
            result={"ok": True},
            done=True,
            trace=(),
        )


class AgentRuntimeRaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as db:
            db.add(AppUser(id="user-1", email="user1@example.com", display_name="User 1"))
            db.add(Tenant(id="workspace-1", name="Workspace 1", slug="workspace-1"))
            db.add(
                TenantMember(
                    id="membership-1",
                    tenant_id="workspace-1",
                    user_id="user-1",
                    role="owner",
                )
            )
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def context(self, db):
        return await resolve_execution_context(
            db,
            workspace_id="workspace-1",
            user_id="user-1",
            channel="web",
            surface=SurfaceKind.WORKSPACE_PRIVATE,
            conversation_id="conversation-1",
            require_membership=True,
        )

    async def create_read_run(self, db, *, run_id: str):
        context = await self.context(db)
        await create_run(
            db,
            context=context,
            plan=AgentPlan(
                run_id=run_id,
                goal=run_id,
                steps=(AgentPlanStep("read", "records.read"),),
            ),
        )
        await db.commit()
        return context

    async def test_database_rejects_mixed_workspace_and_personal_ownership(self):
        async with self.sessions() as db:
            db.add(
                AgentRuntimeRun(
                    id="mixed-owner-run",
                    scope_kind="workspace",
                    workspace_id="workspace-1",
                    owner_user_id="user-1",
                    authority_user_id="user-1",
                    principal_id="user:user-1",
                    conversation_id="conversation-1",
                    source_channel="web",
                    source_surface=SurfaceKind.WORKSPACE_PRIVATE.value,
                    goal="must fail",
                    plan_json="{}",
                    budget_json='{"max_steps":1,"max_mutations":0}',
                    status="queued",
                )
            )
            with self.assertRaises(IntegrityError):
                await db.flush()
            await db.rollback()

    async def test_rightful_worker_can_renew_after_live_cancellation_request(self):
        async with self.sessions() as db:
            context = await self.create_read_run(db, run_id="renew-after-cancel")
            claimed = await claim_run(
                db,
                run_id="renew-after-cancel",
                lease_token="worker-a",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            await db.commit()

            row = await request_cancellation(
                db,
                context=context,
                run_id="renew-after-cancel",
            )
            await db.commit()
            self.assertEqual(row.status, "running")
            self.assertTrue(row.cancellation_requested)

            renewed = await renew_run_lease(
                db,
                run_id="renew-after-cancel",
                lease_token="worker-a",
                lease_seconds=60,
            )
            await db.commit()
            self.assertTrue(renewed)

    async def test_expired_running_lease_cancels_immediately(self):
        async with self.sessions() as db:
            context = await self.create_read_run(db, run_id="expired-cancel")
            row = await claim_run(
                db,
                run_id="expired-cancel",
                lease_token="worker-a",
                lease_seconds=60,
            )
            await db.commit()
            row.lease_until = datetime.utcnow() - timedelta(seconds=1)
            await db.commit()

            cancelled = await request_cancellation(
                db,
                context=context,
                run_id="expired-cancel",
            )
            await db.commit()

            self.assertEqual(cancelled.status, "cancelled")
            self.assertTrue(cancelled.cancellation_requested)
            self.assertIsNone(cancelled.lease_token)
            self.assertIsNone(cancelled.lease_until)
            self.assertIsNotNone(cancelled.finished_at)

    async def test_recovery_worker_terminalizes_abandoned_live_cancellation_without_kernel_call(self):
        kernel = FakeKernel()
        worker = DurableAgentOrchestrator(
            runtime=GovernedAgentRuntime(
                kernel=kernel,
                settings=AgentRuntimeSettings(enabled=True),
            ),
            lease_seconds=60,
        )
        async with self.sessions() as db:
            context = await self.create_read_run(db, run_id="abandoned-cancel")
            row = await claim_run(
                db,
                run_id="abandoned-cancel",
                lease_token="worker-a",
                lease_seconds=60,
            )
            await db.commit()
            await request_cancellation(
                db,
                context=context,
                run_id="abandoned-cancel",
            )
            await db.commit()

            row.lease_until = datetime.utcnow() - timedelta(seconds=1)
            await db.commit()

            result = await worker.run_once(
                db,
                run_id="abandoned-cancel",
                lease_token="worker-b",
            )
            stored = await db.get(AgentRuntimeRun, "abandoned-cancel")

        self.assertIsNotNone(result)
        self.assertEqual(result.status, AgentRunStatus.CANCELLED)
        self.assertEqual(kernel.calls, [])
        self.assertEqual(stored.status, "cancelled")
        self.assertIsNone(stored.lease_token)
        self.assertIsNone(stored.lease_until)


if __name__ == "__main__":
    unittest.main()
