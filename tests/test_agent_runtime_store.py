from __future__ import annotations

from datetime import datetime, timedelta
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.agent_runtime import AgentPlan, AgentPlanStep, AgentStepResult, AgentStepStatus
from packages.agent_runtime.store import (
    AgentRunStateError,
    cancellation_requested,
    claim_run,
    create_run,
    get_run_for_context,
    record_step_result,
    request_cancellation,
    transition_run,
)
from packages.database.agent_runtime_models import (
    AgentRuntimeRun,
    AgentRuntimeStep,
    AgentRuntimeStepAttempt,
)
from packages.database.db import Base
from packages.database.schema import ALEMBIC_HEAD, import_all_models
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


def workspace_context(
    *,
    principal: str = "user:user-1",
    workspace_mode: str = "full",
) -> ExecutionContext:
    return ExecutionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        membership_id=None if workspace_mode == "guest" else "membership-1",
        role="guest" if workspace_mode == "guest" else "owner",
        permissions=frozenset({"workspace:read"}),
        channel="web",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        conversation_id="conversation-1",
        scope_kind=ScopeKind.WORKSPACE,
        principal_id=principal,
        workspace_mode=workspace_mode,
    )


def personal_context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=None,
        user_id="user-personal",
        membership_id=None,
        role="personal_owner",
        permissions=frozenset({"workspace:read"}),
        channel="personal",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id="personal-conversation",
        scope_kind=ScopeKind.PERSONAL,
        principal_id="user:user-personal",
        workspace_mode="personal",
    )


def plan(run_id: str = "agent-run-1") -> AgentPlan:
    return AgentPlan(
        run_id=run_id,
        goal="read then update",
        steps=(
            AgentPlanStep("read", "records.read", {"id": "a"}),
            AgentPlanStep("write", "records.write", {"id": "a", "value": 2}),
        ),
    )


class AgentRuntimeStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_create_run_derives_authority_scope_and_stable_steps(self):
        async with self.sessions() as db:
            row = await create_run(db, context=workspace_context(), plan=plan())
            await db.commit()
            steps = (
                await db.scalars(
                    select(AgentRuntimeStep)
                    .where(AgentRuntimeStep.agent_run_id == row.id)
                    .order_by(AgentRuntimeStep.step_order)
                )
            ).all()

        self.assertEqual(row.scope_kind, "workspace")
        self.assertEqual(row.workspace_id, "workspace-1")
        self.assertIsNone(row.owner_user_id)
        self.assertEqual(row.authority_user_id, "user-1")
        self.assertEqual(row.principal_id, "user:user-1")
        self.assertEqual(row.source_channel, "web")
        self.assertEqual(row.source_surface, SurfaceKind.WORKSPACE_PRIVATE.value)
        self.assertEqual([item.step_id for item in steps], ["read", "write"])
        self.assertEqual(len({item.request_id for item in steps}), 2)
        self.assertTrue(all(len(item.request_id) <= 160 for item in steps))

        columns = set(AgentRuntimeRun.__table__.columns.keys())
        self.assertNotIn("role", columns)
        self.assertNotIn("permissions", columns)
        self.assertNotIn("permissions_json", columns)

    async def test_personal_run_has_no_workspace_authority(self):
        async with self.sessions() as db:
            row = await create_run(
                db,
                context=personal_context(),
                plan=plan("personal-agent-run"),
            )
            await db.commit()

        self.assertEqual(row.scope_kind, "personal")
        self.assertIsNone(row.workspace_id)
        self.assertEqual(row.owner_user_id, "user-personal")
        self.assertEqual(row.principal_id, "user:user-personal")
        self.assertEqual(row.source_surface, SurfaceKind.PERSONAL_PRIVATE.value)

    async def test_guest_workspace_run_is_rejected_until_provenance_is_durable(self):
        async with self.sessions() as db:
            with self.assertRaisesRegex(
                AgentRunStateError,
                "Guest Workspace agent runs require durable external-installation provenance",
            ):
                await create_run(
                    db,
                    context=workspace_context(workspace_mode="guest"),
                    plan=plan("guest-run"),
                )

    async def test_context_lookup_is_principal_and_scope_bound(self):
        async with self.sessions() as db:
            await create_run(db, context=workspace_context(), plan=plan("scoped-run"))
            await db.commit()

            visible = await get_run_for_context(
                db, context=workspace_context(), run_id="scoped-run"
            )
            hidden = await get_run_for_context(
                db,
                context=workspace_context(principal="user:other"),
                run_id="scoped-run",
            )

        self.assertIsNotNone(visible)
        self.assertIsNone(hidden)

    async def test_active_lease_blocks_second_worker_and_expiry_allows_recovery(self):
        async with self.sessions() as db:
            await create_run(db, context=workspace_context(), plan=plan("lease-run"))
            await db.commit()

            first = await claim_run(
                db, run_id="lease-run", lease_token="worker-a", lease_seconds=60
            )
            await db.commit()
            blocked = await claim_run(
                db, run_id="lease-run", lease_token="worker-b", lease_seconds=60
            )
            self.assertIsNotNone(first)
            self.assertIsNone(blocked)

            first.lease_until = datetime.utcnow() - timedelta(seconds=1)
            await db.commit()
            recovered = await claim_run(
                db, run_id="lease-run", lease_token="worker-b", lease_seconds=60
            )
            await db.commit()
            self.assertIsNotNone(recovered)
            await db.refresh(recovered)
            self.assertEqual(recovered.lease_token, "worker-b")
            self.assertIsNotNone(recovered.started_at)

    async def test_cancellation_is_durable_and_scope_bound(self):
        async with self.sessions() as db:
            await create_run(db, context=workspace_context(), plan=plan("cancel-run"))
            await db.commit()

            with self.assertRaises(AgentRunStateError):
                await request_cancellation(
                    db,
                    context=workspace_context(principal="user:other"),
                    run_id="cancel-run",
                )

            row = await request_cancellation(
                db, context=workspace_context(), run_id="cancel-run"
            )
            await db.commit()
            self.assertTrue(row.cancellation_requested)
            self.assertTrue(await cancellation_requested(db, run_id="cancel-run"))
            self.assertIsNone(
                await claim_run(
                    db, run_id="cancel-run", lease_token="worker-a", lease_seconds=60
                )
            )

    async def test_missing_run_cancellation_check_fails_closed(self):
        async with self.sessions() as db:
            with self.assertRaises(AgentRunStateError):
                await cancellation_requested(db, run_id="missing-agent-run")

    async def test_step_attempt_history_preserves_request_identity(self):
        async with self.sessions() as db:
            await create_run(db, context=workspace_context(), plan=plan("attempt-run"))
            await db.commit()
            step = await db.scalar(
                select(AgentRuntimeStep).where(
                    AgentRuntimeStep.agent_run_id == "attempt-run",
                    AgentRuntimeStep.step_id == "write",
                )
            )
            waiting = AgentStepResult(
                step_id="write",
                capability_id="records.write",
                request_id=step.request_id,
                status=AgentStepStatus.WAITING_APPROVAL,
                kernel_run_id=None,
                approval_id=None,
                error_code="approval_required",
                error="approval required",
            )
            await record_step_result(db, run_id="attempt-run", step_result=waiting)
            await db.commit()

            completed = AgentStepResult(
                step_id="write",
                capability_id="records.write",
                request_id=step.request_id,
                status=AgentStepStatus.COMPLETED,
                kernel_run_id=None,
                result={"ok": True},
            )
            await record_step_result(db, run_id="attempt-run", step_result=completed)
            await db.commit()
            attempts = (
                await db.scalars(
                    select(AgentRuntimeStepAttempt)
                    .where(AgentRuntimeStepAttempt.agent_step_id == step.id)
                    .order_by(AgentRuntimeStepAttempt.attempt)
                )
            ).all()

        self.assertEqual([item.attempt for item in attempts], [1, 2])
        self.assertEqual({item.request_id for item in attempts}, {step.request_id})
        self.assertEqual(step.status, "completed")
        self.assertEqual(step.attempt_count, 2)

    async def test_run_state_machine_rejects_terminal_reopen(self):
        async with self.sessions() as db:
            await create_run(db, context=workspace_context(), plan=plan("state-run"))
            await transition_run(db, run_id="state-run", to_status="running")
            await transition_run(db, run_id="state-run", to_status="completed")
            with self.assertRaises(AgentRunStateError):
                await transition_run(db, run_id="state-run", to_status="running")

    def test_schema_head_advances_with_runtime_chat_history(self):
        self.assertEqual(ALEMBIC_HEAD, "0058_agent_chat_history")


if __name__ == "__main__":
    unittest.main()
