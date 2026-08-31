from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select

from packages.database.db import session_scope
from packages.database.kernel_models import KernelApproval
from packages.workflow.engine import queue_workflow_run, workflow_engine
from packages.workflow.models import WorkflowDefinition, WorkflowRun, WorkflowSchedule, WorkflowStepRun
from packages.workflow.spec import next_schedule_time
from packages.workflow.tracing import record_workflow_event


class WorkflowScheduler:
    """Durable DB-leased dispatcher; workflow truth never lives in this process."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_error: str | None = None
        self._last_tick_at: datetime | None = None
        self._poll_seconds = max(1.0, float(os.getenv("OPERLY_WORKFLOW_POLL_SECONDS", "2")))
        # A live action keeps this lease while its provider call is in flight.  We do
        # not automatically replay an expired running action because its external
        # side effect may have committed immediately before a process failure.
        self._lease_seconds = max(300, int(os.getenv("OPERLY_WORKFLOW_LEASE_SECONDS", "3600")))

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="operly-workflow-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None

    def status(self) -> dict[str, object]:
        return {
            "running": bool(self._task and not self._task.done()),
            "poll_seconds": self._poll_seconds,
            "lease_seconds": self._lease_seconds,
            "last_tick_at": self._last_tick_at.isoformat() if self._last_tick_at else None,
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
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> None:
        self._last_tick_at = datetime.utcnow()
        await self._release_decided_approvals_and_waits()
        await self._mark_orphaned_running_runs()
        await self._enqueue_due_schedules()
        run_ids = await self._claim_runs(limit=8)
        for run_id in run_ids:
            try:
                async with session_scope() as db:
                    await workflow_engine.execute_run(db, run_id)
            except Exception as error:
                await self._mark_dispatch_failure(run_id, error)

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
                if run.status == "waiting" and step.wait_until and step.wait_until <= now:
                    run.status = "queued"
                    continue
                if run.status == "waiting_approval" and step.approval_id:
                    approval = await db.get(KernelApproval, step.approval_id)
                    if approval is not None and approval.status != "pending":
                        run.status = "queued"

    async def _mark_orphaned_running_runs(self) -> None:
        """Fail closed when execution ownership is lost; never guess that a write did not happen."""
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
                workflow = await db.get(WorkflowDefinition, run.workflow_id)
                run.status = "orphaned"
                run.error_code = "execution_outcome_uncertain"
                run.error_message = "The worker lease expired during an in-flight step; Operly will not replay it automatically."
                run.finished_at = now
                run.lease_token = None
                run.lease_until = None
                if workflow is not None:
                    await record_workflow_event(
                        db,
                        workspace_id=run.workspace_id,
                        workflow_id=run.workflow_id,
                        workflow_run_id=run.id,
                        event_type="workflow.run.orphaned",
                        actor_id="operly:scheduler",
                        owner_user_id=run.owner_user_id,
                        principal_id=f"user:{run.owner_user_id}" if run.owner_user_id else None,
                        payload={"code": "execution_outcome_uncertain", "current_step_key": run.current_step_key},
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
                        or_(WorkflowSchedule.lease_until.is_(None), WorkflowSchedule.lease_until < now),
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
                if workflow is None or workflow.status != "enabled" or scheduled_for is None:
                    schedule.enabled = False
                    schedule.lease_token = None
                    schedule.lease_until = None
                    continue
                schedule_spec = json.loads(schedule.schedule_json)
                schedule.last_fired_at = scheduled_for
                schedule.next_run_at = next_schedule_time(schedule_spec, after=scheduled_for)
                if schedule.next_run_at is None:
                    schedule.enabled = False
                schedule.lease_token = None
                schedule.lease_until = None
                dedupe_key = f"schedule:{schedule.id}:{scheduled_for.isoformat()}"
                existing = await db.scalar(select(WorkflowRun.id).where(WorkflowRun.dedupe_key == dedupe_key))
                if existing is None:
                    await queue_workflow_run(
                        db,
                        workflow=workflow,
                        trigger_type="schedule",
                        trigger_payload={"schedule_id": schedule.id, "scheduled_for": scheduled_for.isoformat()},
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
                    principal_id=f"user:{workflow.owner_user_id}" if workflow.owner_user_id else None,
                    payload={"schedule_id": schedule.id, "scheduled_for": scheduled_for.isoformat(), "deduplicated": existing is not None, "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None},
                )

    async def _claim_runs(self, *, limit: int) -> list[str]:
        now = datetime.utcnow()
        token = str(uuid4())
        async with session_scope() as db:
            rows = (
                await db.scalars(
                    select(WorkflowRun)
                    .where(
                        WorkflowRun.status == "queued",
                        or_(WorkflowRun.lease_until.is_(None), WorkflowRun.lease_until < now),
                    )
                    .order_by(WorkflowRun.created_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            ids: list[str] = []
            for run in rows:
                run.lease_token = token
                run.lease_until = now + timedelta(seconds=self._lease_seconds)
                ids.append(run.id)
            return ids

    async def _mark_dispatch_failure(self, run_id: str, error: Exception) -> None:
        async with session_scope() as db:
            run = await db.get(WorkflowRun, run_id)
            if run is None or run.status in {"completed", "cancelled", "failed", "orphaned", "waiting", "waiting_approval"}:
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
                    owner_user_id=run.owner_user_id,
                    principal_id=f"user:{run.owner_user_id}" if run.owner_user_id else None,
                    payload={"code": "dispatcher_failure"},
                )


workflow_scheduler = WorkflowScheduler()
