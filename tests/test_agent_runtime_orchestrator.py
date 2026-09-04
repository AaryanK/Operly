from __future__ import annotations

import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.agent_runtime import (
    AgentPlan,
    AgentPlanStep,
    AgentRunStatus,
    AgentRuntimeSettings,
    GovernedAgentRuntime,
)
from packages.agent_runtime.orchestrator import AgentLeaseLost, DurableAgentOrchestrator
from packages.agent_runtime.store import AgentRunStateError, create_run, queue_after_approval
from packages.database.agent_runtime_models import AgentRuntimeRun, AgentRuntimeStep
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
from packages.kernel.runtime import RuntimeExecutionError
from packages.security.execution_context import resolve_execution_context
from packages.security.surfaces import SurfaceKind


def capability(capability_id: str, *, risk: CapabilityRisk) -> CapabilitySpec:
    return CapabilitySpec(
        id=capability_id,
        version="1",
        display_name=capability_id,
        description="test capability",
        provider_id="fake",
        scopes=frozenset({"workspace"}),
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        risk=risk,
        approval_required=risk is not CapabilityRisk.READ_ONLY,
    )


class FakeKernel:
    def __init__(self) -> None:
        self.registry = CapabilityRegistry()
        self.registry.register(capability("records.read", risk=CapabilityRisk.READ_ONLY))
        self.registry.register(capability("records.write", risk=CapabilityRisk.MEDIUM))
        self.calls: list[tuple[object, object]] = []
        self.role_after_first: str | None = None
        self.delete_membership_after_first = False
        self.require_approval = False
        self.steal_lease_after_first = False
        self.approval_id = "11111111-1111-1111-1111-111111111111"

    async def execute(self, db, *, context, request):
        self.calls.append((context, request))
        call_number = len(self.calls)
        if self.require_approval and request.capability_id == "records.write" and not request.approval_id:
            raise RuntimeExecutionError(
                "Approval is required before this capability can run",
                run_id="22222222-2222-2222-2222-222222222222",
                code="approval_required",
                status_code=409,
                approval_id=self.approval_id,
            )

        if call_number == 1 and (self.role_after_first or self.delete_membership_after_first):
            membership = await db.scalar(
                select(TenantMember).where(
                    TenantMember.tenant_id == "workspace-1",
                    TenantMember.user_id == "user-1",
                )
            )
            if self.delete_membership_after_first:
                await db.delete(membership)
            elif self.role_after_first:
                membership.role = self.role_after_first
            await db.flush()

        if call_number == 1 and self.steal_lease_after_first:
            run = await db.get(AgentRuntimeRun, request.goal)
            if run is not None:
                run.lease_token = "worker-b"
                await db.flush()

        return RuntimeResponse(
            run_id=f"33333333-3333-3333-3333-{call_number:012d}",
            status="completed",
            capability_id=request.capability_id,
            decision=AuthorizationDecision.ALLOW,
            result={"ok": True, "role": context.role},
            done=True,
            trace=(),
        )


class AgentRuntimeOrchestratorTests(unittest.IsolatedAsyncioTestCase):
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

    async def context(self, db, *, surface: SurfaceKind = SurfaceKind.WORKSPACE_PRIVATE):
        return await resolve_execution_context(
            db,
            workspace_id="workspace-1",
            user_id="user-1",
            channel="web",
            surface=surface,
            conversation_id="conversation-1",
            require_membership=True,
        )

    def worker(self, kernel: FakeKernel) -> DurableAgentOrchestrator:
        return DurableAgentOrchestrator(
            runtime=GovernedAgentRuntime(
                kernel=kernel,
                settings=AgentRuntimeSettings(enabled=True),
            ),
            lease_seconds=300,
        )

    async def create_workspace_run(self, db, *, run_id: str, steps: tuple[AgentPlanStep, ...]):
        context = await self.context(db)
        await create_run(
            db,
            context=context,
            plan=AgentPlan(run_id=run_id, goal=run_id, steps=steps),
        )
        await db.commit()
        return context

    async def test_authority_is_re_resolved_before_every_step(self):
        kernel = FakeKernel()
        kernel.role_after_first = "employee"
        async with self.sessions() as db:
            await self.create_workspace_run(
                db,
                run_id="role-refresh-run",
                steps=(
                    AgentPlanStep("read-1", "records.read"),
                    AgentPlanStep("read-2", "records.read"),
                ),
            )
            result = await self.worker(kernel).run_once(
                db, run_id="role-refresh-run", lease_token="worker-a"
            )

        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        self.assertEqual([context.role for context, _ in kernel.calls], ["owner", "employee"])

    async def test_membership_revocation_stops_before_next_capability(self):
        kernel = FakeKernel()
        kernel.delete_membership_after_first = True
        async with self.sessions() as db:
            await self.create_workspace_run(
                db,
                run_id="revoked-run",
                steps=(
                    AgentPlanStep("read-1", "records.read"),
                    AgentPlanStep("read-2", "records.read"),
                ),
            )
            result = await self.worker(kernel).run_once(
                db, run_id="revoked-run", lease_token="worker-a"
            )
            row = await db.get(AgentRuntimeRun, "revoked-run")

        self.assertEqual(result.status, AgentRunStatus.FAILED)
        self.assertEqual(result.error_code, "authority_unavailable")
        self.assertEqual(len(kernel.calls), 1)
        self.assertEqual(row.status, "failed")

    async def test_approval_resume_reuses_same_kernel_request_identity(self):
        kernel = FakeKernel()
        kernel.require_approval = True
        async with self.sessions() as db:
            context = await self.create_workspace_run(
                db,
                run_id="approval-run",
                steps=(
                    AgentPlanStep("write", "records.write", {"value": 7}),
                    AgentPlanStep("read", "records.read"),
                ),
            )
            waiting = await self.worker(kernel).run_once(
                db, run_id="approval-run", lease_token="worker-a"
            )
            self.assertEqual(waiting.status, AgentRunStatus.WAITING_APPROVAL)
            first_request_id = kernel.calls[0][1].request_id

            await queue_after_approval(
                db,
                context=context,
                run_id="approval-run",
                approval_id=kernel.approval_id,
            )
            await db.commit()
            completed = await self.worker(kernel).run_once(
                db, run_id="approval-run", lease_token="worker-b"
            )
            step = await db.scalar(
                select(AgentRuntimeStep).where(
                    AgentRuntimeStep.agent_run_id == "approval-run",
                    AgentRuntimeStep.step_id == "write",
                )
            )

        self.assertEqual(completed.status, AgentRunStatus.COMPLETED)
        self.assertEqual(kernel.calls[1][1].request_id, first_request_id)
        self.assertEqual(kernel.calls[1][1].approval_id, kernel.approval_id)
        self.assertEqual(step.attempt_count, 2)

    async def test_delegated_mcp_surface_cannot_become_durable_agent_authority(self):
        async with self.sessions() as db:
            delegated = await self.context(db, surface=SurfaceKind.MCP_CLIENT)
            with self.assertRaisesRegex(
                AgentRunStateError,
                "Delegated/external Workspace surfaces require durable delegation provenance",
            ):
                await create_run(
                    db,
                    context=delegated,
                    plan=AgentPlan(
                        run_id="delegated-run",
                        goal="must fail closed",
                        steps=(AgentPlanStep("read", "records.read"),),
                    ),
                )

    async def test_lost_lease_stops_orchestrator_after_kernel_step(self):
        kernel = FakeKernel()
        kernel.steal_lease_after_first = True
        async with self.sessions() as db:
            await self.create_workspace_run(
                db,
                run_id="lease-loss-run",
                steps=(AgentPlanStep("read", "records.read"),),
            )
            with self.assertRaises(AgentLeaseLost):
                await self.worker(kernel).run_once(
                    db, run_id="lease-loss-run", lease_token="worker-a"
                )

        self.assertEqual(len(kernel.calls), 1)

    async def test_queued_cancellation_is_terminal_without_worker_claim(self):
        async with self.sessions() as db:
            context = await self.create_workspace_run(
                db,
                run_id="cancel-before-run",
                steps=(AgentPlanStep("read", "records.read"),),
            )
            from packages.agent_runtime.store import request_cancellation

            row = await request_cancellation(
                db, context=context, run_id="cancel-before-run"
            )
            await db.commit()

        self.assertEqual(row.status, "cancelled")
        self.assertTrue(row.cancellation_requested)
        self.assertIsNotNone(row.finished_at)

    def test_approval_id_contract_matches_kernel_uuid_width(self):
        with self.assertRaises(ValueError):
            AgentPlanStep("write", "records.write", approval_id="x" * 37)


if __name__ == "__main__":
    unittest.main()
