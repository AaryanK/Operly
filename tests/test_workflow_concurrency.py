import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import select

from packages.database.db import Base, SessionFactory, engine
from packages.database.kernel_models import KernelEventRecord
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind
from packages.workflow import workflow_capabilities
from packages.workflow.concurrency import (
    ConcurrentWorkflowScheduler,
    WorkflowProvider,
    normalize_concurrency_policy,
    policy_from_snapshot,
)
from packages.workflow.engine import queue_workflow_run
from packages.workflow.models import (
    WorkflowDefinition,
    WorkflowEventCursor,
    WorkflowEventTrigger,
    WorkflowRun,
    WorkflowTraceEvent,
    WorkflowVersion,
)
from packages.workflow.triggers import workflow_event_dispatcher


class WorkflowConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    async def _identity(self, db, slug: str):
        user = AppUser(
            email=f"{slug}@example.com",
            display_name=slug,
        )
        workspace = Tenant(name=slug, slug=slug)
        db.add_all([user, workspace])
        await db.flush()
        member = TenantMember(tenant_id=workspace.id, user_id=user.id, role="owner")
        db.add(member)
        await db.flush()
        return user, workspace, member

    async def _workflow(
        self,
        db,
        *,
        user,
        workspace,
        name: str,
        max_concurrent_runs: int,
        overflow_policy: str,
        enabled: bool = True,
    ):
        workflow = WorkflowDefinition(
            scope_kind="workspace",
            workspace_id=workspace.id,
            owner_user_id=user.id,
            name=name,
            description="concurrency test",
            status="enabled" if enabled else "disabled",
            current_version=1,
        )
        db.add(workflow)
        await db.flush()
        snapshot = {
            "name": name,
            "description": "concurrency test",
            "spec": {"steps": [{"id": "wait", "kind": "wait", "seconds": 1}]},
            "schedule": None,
            "status": workflow.status,
            "concurrency": {
                "max_concurrent_runs": max_concurrent_runs,
                "overflow_policy": overflow_policy,
            },
        }
        version = WorkflowVersion(
            workflow_id=workflow.id,
            version=1,
            spec_json=json.dumps(snapshot["spec"]),
            snapshot_json=json.dumps(snapshot),
            created_by_user_id=user.id,
        )
        db.add(version)
        await db.flush()
        return workflow

    async def _queue(self, db, workflow, count: int):
        rows = []
        for index in range(count):
            rows.append(
                await queue_workflow_run(
                    db,
                    workflow=workflow,
                    trigger_type="manual",
                    trigger_payload={"index": index},
                    initiated_by_user_id=workflow.owner_user_id,
                    dedupe_key=f"concurrency:{workflow.id}:{index}",
                )
            )
        return rows

    def test_policy_normalization_and_capability_contract(self):
        self.assertEqual(
            normalize_concurrency_policy(None),
            {"max_concurrent_runs": 0, "overflow_policy": "queue"},
        )
        self.assertEqual(
            normalize_concurrency_policy(
                {"max_concurrent_runs": 3, "overflow_policy": "DROP"}
            ),
            {"max_concurrent_runs": 3, "overflow_policy": "drop"},
        )
        self.assertEqual(
            normalize_concurrency_policy(
                {"max_concurrent_runs": 0, "overflow_policy": "drop"}
            ),
            {"max_concurrent_runs": 0, "overflow_policy": "queue"},
        )
        with self.assertRaises(ValueError):
            normalize_concurrency_policy({"max_concurrent_runs": 65})
        with self.assertRaises(ValueError):
            normalize_concurrency_policy({"overflow_policy": "cancel_previous"})

        specs = {spec.id: spec for spec in workflow_capabilities()}
        for capability_id in ("workflow.create", "workflow.update"):
            concurrency = specs[capability_id].input_schema["properties"]["concurrency"]
            self.assertEqual(concurrency["properties"]["max_concurrent_runs"]["maximum"], 64)
            self.assertEqual(
                concurrency["properties"]["overflow_policy"]["enum"],
                ["queue", "drop"],
            )

    async def test_queue_policy_is_per_workflow_and_cross_replica_safe(self):
        async with SessionFactory() as db:
            user, workspace, _member = await self._identity(db, "queue-policy")
            first = await self._workflow(
                db,
                user=user,
                workspace=workspace,
                name="first",
                max_concurrent_runs=1,
                overflow_policy="queue",
            )
            second = await self._workflow(
                db,
                user=user,
                workspace=workspace,
                name="second",
                max_concurrent_runs=1,
                overflow_policy="queue",
            )
            await self._queue(db, first, 4)
            await self._queue(db, second, 4)
            await db.commit()

        scheduler_a = ConcurrentWorkflowScheduler()
        scheduler_b = ConcurrentWorkflowScheduler()
        claims_a = await scheduler_a._claim_runs(limit=8)
        self.assertEqual(len(claims_a), 2)

        # A second scheduler process sees the durable leases and per-workflow slots,
        # not process-local state, so it cannot claim a second run from either workflow.
        claims_b = await scheduler_b._claim_runs(limit=8)
        self.assertEqual(claims_b, [])

        async with SessionFactory() as db:
            claimed = (await db.scalars(select(WorkflowRun).where(WorkflowRun.id.in_([item[0] for item in claims_a])))).all()
            self.assertEqual({row.workflow_id for row in claimed}, {first.id, second.id})
            released = claimed[0]
            released.status = "completed"
            released.finished_at = datetime.utcnow()
            released.lease_token = None
            released.lease_until = None
            await db.commit()

        claims_after_release = await scheduler_b._claim_runs(limit=8)
        self.assertEqual(len(claims_after_release), 1)

    async def test_waiting_and_approval_pauses_occupy_concurrency_slots(self):
        async with SessionFactory() as db:
            user, workspace, _member = await self._identity(db, "waiting-policy")
            workflow = await self._workflow(
                db,
                user=user,
                workspace=workspace,
                name="serial",
                max_concurrent_runs=1,
                overflow_policy="queue",
            )
            await self._queue(db, workflow, 3)
            await db.commit()

        scheduler = ConcurrentWorkflowScheduler()
        first_claim = await scheduler._claim_runs(limit=8)
        self.assertEqual(len(first_claim), 1)

        async with SessionFactory() as db:
            run = await db.get(WorkflowRun, first_claim[0][0])
            run.status = "waiting_approval"
            run.lease_token = None
            run.lease_until = None
            await db.commit()

        self.assertEqual(await scheduler._claim_runs(limit=8), [])

        async with SessionFactory() as db:
            run = await db.get(WorkflowRun, first_claim[0][0])
            run.status = "completed"
            run.finished_at = datetime.utcnow()
            await db.commit()

        self.assertEqual(len(await scheduler._claim_runs(limit=8)), 1)

    async def test_drop_policy_preserves_suppression_evidence(self):
        async with SessionFactory() as db:
            user, workspace, _member = await self._identity(db, "drop-policy")
            workflow = await self._workflow(
                db,
                user=user,
                workspace=workspace,
                name="drop-overflow",
                max_concurrent_runs=2,
                overflow_policy="drop",
            )
            await self._queue(db, workflow, 7)
            await db.commit()

        scheduler = ConcurrentWorkflowScheduler()
        claims = await scheduler._claim_runs(limit=8)
        self.assertEqual(len(claims), 2)

        async with SessionFactory() as db:
            rows = (
                await db.scalars(
                    select(WorkflowRun)
                    .where(WorkflowRun.workflow_id == workflow.id)
                    .order_by(WorkflowRun.created_at)
                )
            ).all()
            suppressed = [row for row in rows if row.error_code == "concurrency_suppressed"]
            self.assertEqual(len(suppressed), 5)
            self.assertTrue(all(row.status == "cancelled" for row in suppressed))
            traces = (
                await db.scalars(
                    select(WorkflowTraceEvent).where(
                        WorkflowTraceEvent.workflow_id == workflow.id,
                        WorkflowTraceEvent.event_type == "workflow.run.suppressed",
                    )
                )
            ).all()
            self.assertEqual(len(traces), 5)

    async def test_event_trigger_storm_respects_workflow_limit(self):
        baseline = datetime.utcnow() - timedelta(seconds=2)
        async with SessionFactory() as db:
            user, workspace, _member = await self._identity(db, "event-storm")
            workflow = await self._workflow(
                db,
                user=user,
                workspace=workspace,
                name="event storm",
                max_concurrent_runs=2,
                overflow_policy="queue",
            )
            db.add(
                WorkflowEventTrigger(
                    workflow_id=workflow.id,
                    event_pattern="stress.concurrent.fire",
                    condition_json="{}",
                    enabled=True,
                    created_by_user_id=user.id,
                )
            )
            db.add(
                WorkflowEventCursor(
                    id="kernel",
                    last_created_at=baseline,
                    last_event_id="",
                )
            )
            for index in range(20):
                db.add(
                    KernelEventRecord(
                        event_type="stress.concurrent.fire",
                        scope_kind="workspace",
                        workspace_id=workspace.id,
                        owner_user_id=None,
                        principal_id=f"user:{user.id}",
                        actor_type="system",
                        actor_id="concurrency-test",
                        initiator_principal_id=f"user:{user.id}",
                        executor_principal_id="concurrency-test",
                        capability_id="stress.concurrent.seed",
                        resource_type="stress_event",
                        resource_id=str(index),
                        payload_json=json.dumps({"index": index}),
                    )
                )
            await db.commit()

        queued = await workflow_event_dispatcher.tick()
        self.assertEqual(queued, 20)

        scheduler = ConcurrentWorkflowScheduler()
        claims = await scheduler._claim_runs(limit=16)
        self.assertEqual(len(claims), 2)

        async with SessionFactory() as db:
            all_runs = (
                await db.scalars(
                    select(WorkflowRun).where(WorkflowRun.workflow_id == workflow.id)
                )
            ).all()
            self.assertEqual(len(all_runs), 20)
            leased = [row for row in all_runs if row.lease_token]
            waiting_in_queue = [
                row
                for row in all_runs
                if row.status == "queued" and row.lease_token is None
            ]
            self.assertEqual(len(leased), 2)
            self.assertEqual(len(waiting_in_queue), 18)

    async def test_workflow_update_preserves_or_changes_immutable_policy_snapshot(self):
        async with SessionFactory() as db:
            user, workspace, member = await self._identity(db, "provider-policy")
            workflow = await self._workflow(
                db,
                user=user,
                workspace=workspace,
                name="provider",
                max_concurrent_runs=2,
                overflow_policy="drop",
            )
            await db.commit()

            context = ExecutionContext(
                workspace_id=workspace.id,
                user_id=user.id,
                membership_id=member.id,
                role="owner",
                permissions=frozenset({"workflows:read", "workflows:write", "workflows:run"}),
                channel="test",
                surface=SurfaceKind.SYSTEM_TASK,
                scope_kind=ScopeKind.WORKSPACE,
                principal_id=f"user:{user.id}",
            )
            specs = {spec.id: spec for spec in workflow_capabilities()}
            provider = WorkflowProvider()

            await provider.execute(
                db,
                context=context,
                capability=specs["workflow.update"],
                arguments={"workflow_id": workflow.id, "name": "provider renamed"},
                minimum_context={},
            )
            await db.flush()
            await db.refresh(workflow)
            self.assertEqual(workflow.current_version, 2)
            version_two = await db.scalar(
                select(WorkflowVersion).where(
                    WorkflowVersion.workflow_id == workflow.id,
                    WorkflowVersion.version == 2,
                )
            )
            self.assertEqual(
                policy_from_snapshot(json.loads(version_two.snapshot_json)),
                {"max_concurrent_runs": 2, "overflow_policy": "drop"},
            )

            result = await provider.execute(
                db,
                context=context,
                capability=specs["workflow.update"],
                arguments={
                    "workflow_id": workflow.id,
                    "concurrency": {
                        "max_concurrent_runs": 4,
                        "overflow_policy": "queue",
                    },
                },
                minimum_context={},
            )
            await db.flush()
            await db.refresh(workflow)
            self.assertEqual(workflow.current_version, 3)
            self.assertEqual(
                result.value["workflow"]["concurrency"],
                {"max_concurrent_runs": 4, "overflow_policy": "queue"},
            )
            version_three = await db.scalar(
                select(WorkflowVersion).where(
                    WorkflowVersion.workflow_id == workflow.id,
                    WorkflowVersion.version == 3,
                )
            )
            self.assertEqual(
                policy_from_snapshot(json.loads(version_three.snapshot_json)),
                {"max_concurrent_runs": 4, "overflow_policy": "queue"},
            )


if __name__ == "__main__":
    unittest.main()
