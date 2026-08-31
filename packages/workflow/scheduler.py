from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select

from packages.database.db import DATABASE_URL, session_scope
from packages.database.kernel_models import KernelApproval
from packages.workflow.engine import queue_workflow_run, workflow_engine
from packages.workflow.models import (
    WorkflowDefinition,
    WorkflowRun,
    WorkflowSchedule,
    WorkflowStepAttempt,
    WorkflowStepRun,
)
from packages.workflow.spec import next_schedule_time
from packages.workflow.tracing import record_workflow_event


class WorkflowScheduler:
    """Durable DB-leased dispatcher; workflow truth never lives in this process."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._workers: set[asyncio.Task] = set()
        self._stop = asyncio.Event()
        self._last_error: str | None = None
        self._last_tick_at: datetime | None = None
        self._poll_seconds = max(
            1.0, float(os.getenv("OPERLY_WORKFLOW_POLL_SECONDS", "2"))
        )
        self._lease_seconds = max(
            300, int(os.getenv("OPERLY_WORKFLOW_LEASE_SECONDS", "3600"))
        )
        self._heartbeat_seconds = max(
            10.0,
            min(
                float(self._lease_seconds) / 3.0,
                float(os.getenv("OPERLY_WORKFLOW_HEARTBEAT_SECONDS", "60")),
            ),
        )
        # SQLite is the development fallback and serializes writes. Keep it deliberately
        # single-worker unless a developer opts in; production PostgreSQL defaults to
        # bounded concurrency.
        default_workers = "1" if DATABASE_URL.startswith("sqlite") else "8"
        self._max_workers = max(
            1,
            min(
                int(os.getenv("OPERLY_WORKFLOW_MAX_WORKERS", default_workers)),
                64,
            ),
        )

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self._loop(), name="operly-workflow-scheduler"
        )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._task = None

        workers = list(self._workers)
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._workers.clear()

    def status(self) -> dict[str, object]:
        return {
            "running": bool(self._task and not self._task.done()),
            "poll_seconds": self._poll_seconds,
            "lease_seconds": self._lease_seconds,
            "heartbeat_seconds": self._heartbeat_seconds,
            "max_workers": self._max_workers,
            "active_workers": sum(1 for task in self._workers if not task.done()),
            "last_tick_at": (
                self._last_tick_at.isoformat() if self._last_tick_at else None
            ),
            "last_error": self._last_error,
        }

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._last_error = f"{type(error).__name__}: {error}"[:500]
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._poll_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> None:
        self._last_tick_at = datetime.utcnow()
        await self._release_decided_approvals_and_waits()
        await self._mark_orphaned_running_runs()
        await self._enqueue_due_schedules()

        active = sum(1 for task in self._workers if not task.done())
        capacity = max(0, self._max_workers - active)
        if capacity < 1:
            return
        claims = await self._claim_runs(limit=min(capacity, 25))
        for run_id, lease_token in claims:
            task = asyncio.create_task(
                self._execute_claimed_run(run_id, lease_token),
                name=f"operly-workflow-run:{run_id}",
            )
            self._workers.add(task)
            task.add_done_callback(self._workers.discard)

    async def _execute_claimed_run(self, run_id: str, lease_token: str) -> None:
        heartbeat = asyncio.create_task(
            self._lease_heartbeat(run_id, lease_token),
            name=f"operly-workflow-heartbeat:{run_id}",
        )
        try:
            async with session_scope() as db:
                owned = await db.scalar(
                    select(WorkflowRun.id).where(
                        WorkflowRun.id == run_id,
                        WorkflowRun.lease_token == lease_token,
                        WorkflowRun.status == "queued",
                    )
                )
                if owned is None:
                    return
                await workflow_engine.execute_run(
                    db,
                    run_id,
                    expected_lease_token=lease_token,
                )
        except asyncio.CancelledError:
            await self._handle_cancelled_worker(run_id, lease_token)
            raise
        except Exception as error:
            await self._mark_dispatch_failure(
                run_id, error, lease_token=lease_token
            )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            except Exception as error:
                # A heartbeat failure must never mask the actual workflow worker
                # outcome during task cleanup.
                self._last_error = (
                    f"heartbeat {run_id}: {type(error).__name__}: {error}"
                )[:500]

    async def _lease_heartbeat(self, run_id: str, lease_token: str) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                async with session_scope() as db:
                    run = await db.scalar(
                        select(WorkflowRun).where(
                            WorkflowRun.id == run_id,
                            WorkflowRun.lease_token == lease_token,
                            WorkflowRun.status.in_(["queued", "running"]),
                        )
                    )
                    if run is None:
                        return
                    run.lease_until = datetime.utcnow() + timedelta(
                        seconds=self._lease_seconds
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Keep retrying on transient DB errors. If connectivity remains broken
                # past the lease, another scheduler will fail closed to orphaned rather
                # than replaying an uncertain external side effect.
                self._last_error = (
                    f"heartbeat {run_id}: {type(error).__name__}: {error}"
                )[:500]

    async def _release_decided_approvals_and_waits(self) -> None:
        now = datetime.utcnow()
        async with session_scope() as db:
            waiting = (
                await db.scalars(
                    select(WorkflowRun)
                    .where(WorkflowRun.status.in_(["waiting", "waiting_approval"]))
                    .order_by(WorkflowRun.updated_at)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for run in waiting:
                step = await db.scalar(
                    select(WorkflowStepRun).where(
                        WorkflowStepRun.workflow_run_id == run.id,
                        WorkflowStepRun.step_key == run.current_step_key,
                    )
                )
                if step is None:
                    run.status = "queued"
                    continue
                if (
                    run.status == "waiting"
                    and step.wait_until
                    and step.wait_until <= now
                ):
                    run.status = "queued"
                    continue
                if run.status == "waiting_approval" and step.approval_id:
                    approval = await db.get(KernelApproval, step.approval_id)
                    if approval is not None and approval.status != "pending":
                        run.status = "queued"

    async def _current_attempt(
        self, db, run: WorkflowRun
    ) -> WorkflowStepAttempt | None:
        if not run.current_step_key:
            return None
        step = await db.scalar(
            select(WorkflowStepRun).where(
                WorkflowStepRun.workflow_run_id == run.id,
                WorkflowStepRun.step_key == run.current_step_key,
            )
        )
        if step is None or step.step_kind != "action" or step.attempt < 1:
            return None
        return await db.scalar(
            select(WorkflowStepAttempt).where(
                WorkflowStepAttempt.step_run_id == step.id,
                WorkflowStepAttempt.attempt == step.attempt,
            )
        )

    async def _orphan_run(
        self,
        db,
        run: WorkflowRun,
        *,
        code: str,
        message: str,
        actor_id: str,
    ) -> None:
        workflow = await db.get(WorkflowDefinition, run.workflow_id)
        attempt = await self._current_attempt(db, run)
        now = datetime.utcnow()
        run.status = "orphaned"
        run.error_code = code
        run.error_message = message
        run.finished_at = now
        run.lease_token = None
        run.lease_until = None
        if attempt is not None and attempt.status == "running":
            attempt.status = "orphaned"
            attempt.error_code = code
            attempt.error_message = message
            attempt.finished_at = now
        if workflow is not None:
            await record_workflow_event(
                db,
                workspace_id=run.workspace_id,
                workflow_id=run.workflow_id,
                workflow_run_id=run.id,
                step_attempt_id=attempt.id if attempt else None,
                event_type="workflow.run.orphaned",
                actor_id=actor_id,
                owner_user_id=run.authority_user_id,
                principal_id=(
                    f"user:{run.authority_user_id}"
                    if run.authority_user_id
                    else None
                ),
                capability_id=attempt.capability_id if attempt else None,
                kernel_run_id=attempt.kernel_run_id if attempt else None,
                approval_id=attempt.approval_id if attempt else None,
                payload={
                    "code": code,
                    "current_step_key": run.current_step_key,
                    "attempt": attempt.attempt if attempt else None,
                },
            )

    async def _mark_orphaned_running_runs(self) -> None:
        """Fail closed when execution ownership is lost; never guess a write did not happen."""

        now = datetime.utcnow()
        async with session_scope() as db:
            rows = (
                await db.scalars(
                    select(WorkflowRun)
                    .where(
                        WorkflowRun.status == "running",
                        WorkflowRun.lease_until.is_not(None),
                        WorkflowRun.lease_until < now,
                    )
                    .limit(50)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for run in rows:
                await self._orphan_run(
                    db,
                    run,
                    code="execution_outcome_uncertain",
                    message=(
                        "The worker lease expired during an in-flight step; Operly "
                        "will not replay it automatically."
                    ),
                    actor_id="operly:scheduler",
                )

    async def _handle_cancelled_worker(
        self, run_id: str, lease_token: str
    ) -> None:
        async with session_scope() as db:
            run = await db.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.id == run_id,
                    WorkflowRun.lease_token == lease_token,
                )
            )
            if run is None:
                return
            if run.status == "queued":
                # The engine never started, so no side effect is uncertain.
                run.lease_token = None
                run.lease_until = None
                return
            if run.status == "running":
                await self._orphan_run(
                    db,
                    run,
                    code="worker_shutdown_outcome_uncertain",
                    message=(
                        "The executing Operly worker stopped while this step was in "
                        "flight; the action will not be replayed automatically."
                    ),
                    actor_id="operly:scheduler",
                )

    async def _enqueue_due_schedules(self) -> None:
        now = datetime.utcnow()
        lease_token = str(uuid4())
        async with session_scope() as db:
            due = (
                await db.scalars(
                    select(WorkflowSchedule)
                    .where(
                        WorkflowSchedule.enabled.is_(True),
                        WorkflowSchedule.next_run_at.is_not(None),
                        WorkflowSchedule.next_run_at <= now,
                        or_(
                            WorkflowSchedule.lease_until.is_(None),
                            WorkflowSchedule.lease_until < now,
                        ),
                    )
                    .order_by(WorkflowSchedule.next_run_at)
                    .limit(25)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for schedule in due:
                schedule.lease_token = lease_token
                schedule.lease_until = now + timedelta(seconds=self._lease_seconds)
            await db.flush()

            for schedule in due:
                scheduled_for = schedule.next_run_at
                workflow = await db.get(WorkflowDefinition, schedule.workflow_id)
                if (
                    workflow is None
                    or workflow.status != "enabled"
                    or scheduled_for is None
                ):
                    schedule.enabled = False
                    schedule.lease_token = None
                    schedule.lease_until = None
                    continue
                schedule_spec = json.loads(schedule.schedule_json)
                schedule.last_fired_at = scheduled_for
                schedule.next_run_at = next_schedule_time(
                    schedule_spec, after=scheduled_for
                )
                exhausted = schedule.next_run_at is None
                if exhausted:
                    schedule.enabled = False
                    workflow.status = "disabled"
                schedule.lease_token = None
                schedule.lease_until = None
                dedupe_key = f"schedule:{schedule.id}:{scheduled_for.isoformat()}"
                existing = await db.scalar(
                    select(WorkflowRun.id).where(
                        WorkflowRun.dedupe_key == dedupe_key
                    )
                )
                if existing is None:
                    await queue_workflow_run(
                        db,
                        workflow=workflow,
                        trigger_type="schedule",
                        trigger_payload={
                            "schedule_id": schedule.id,
                            "scheduled_for": scheduled_for.isoformat(),
                        },
                        initiated_by_user_id=None,
                        dedupe_key=dedupe_key,
                        scheduled_for=scheduled_for,
                    )
                await record_workflow_event(
                    db,
                    workspace_id=workflow.workspace_id,
                    workflow_id=workflow.id,
                    event_type="workflow.schedule.fired",
                    actor_id="operly:scheduler",
                    owner_user_id=workflow.owner_user_id,
                    principal_id=(
                        f"user:{workflow.owner_user_id}"
                        if workflow.owner_user_id
                        else None
                    ),
                    payload={
                        "schedule_id": schedule.id,
                        "scheduled_for": scheduled_for.isoformat(),
                        "deduplicated": existing is not None,
                        "exhausted": exhausted,
                        "next_run_at": (
                            schedule.next_run_at.isoformat()
                            if schedule.next_run_at
                            else None
                        ),
                    },
                )
                if exhausted:
                    await record_workflow_event(
                        db,
                        workspace_id=workflow.workspace_id,
                        workflow_id=workflow.id,
                        event_type="workflow.schedule.exhausted",
                        actor_id="operly:scheduler",
                        owner_user_id=workflow.owner_user_id,
                        principal_id=(
                            f"user:{workflow.owner_user_id}"
                            if workflow.owner_user_id
                            else None
                        ),
                        payload={
                            "schedule_id": schedule.id,
                            "scheduled_for": scheduled_for.isoformat(),
                        },
                    )

    async def _claim_runs(self, *, limit: int) -> list[tuple[str, str]]:
        now = datetime.utcnow()
        async with session_scope() as db:
            rows = (
                await db.scalars(
                    select(WorkflowRun)
                    .where(
                        WorkflowRun.status == "queued",
                        or_(
                            WorkflowRun.lease_until.is_(None),
                            WorkflowRun.lease_until < now,
                        ),
                    )
                    .order_by(WorkflowRun.created_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            claims: list[tuple[str, str]] = []
            for run in rows:
                token = str(uuid4())
                run.lease_token = token
                run.lease_until = now + timedelta(seconds=self._lease_seconds)
                claims.append((run.id, token))
            return claims

    async def _mark_dispatch_failure(
        self,
        run_id: str,
        error: Exception,
        *,
        lease_token: str | None = None,
    ) -> None:
        async with session_scope() as db:
            statement = select(WorkflowRun).where(WorkflowRun.id == run_id)
            if lease_token:
                statement = statement.where(WorkflowRun.lease_token == lease_token)
            run = await db.scalar(statement)
            if run is None or run.status in {
                "completed",
                "completed_with_errors",
                "cancelled",
                "failed",
                "orphaned",
                "waiting",
                "waiting_approval",
            }:
                return
            workflow = await db.get(WorkflowDefinition, run.workflow_id)
            run.status = "failed"
            run.error_code = "dispatcher_failure"
            run.error_message = f"{type(error).__name__}: {error}"[:1000]
            run.finished_at = datetime.utcnow()
            run.lease_token = None
            run.lease_until = None
            if workflow is not None:
                await record_workflow_event(
                    db,
                    workspace_id=run.workspace_id,
                    workflow_id=run.workflow_id,
                    workflow_run_id=run.id,
                    event_type="workflow.run.failed",
                    actor_id="operly:scheduler",
                    owner_user_id=run.authority_user_id,
                    principal_id=(
                        f"user:{run.authority_user_id}"
                        if run.authority_user_id
                        else None
                    ),
                    payload={"code": "dispatcher_failure"},
                )


workflow_scheduler = WorkflowScheduler()
