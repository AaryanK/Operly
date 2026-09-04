from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select

from packages.database.db import session_scope
from packages.database.kernel_models import KernelEventRecord
from packages.workflow.engine import queue_workflow_run
from packages.workflow.models import (
    WorkflowDefinition,
    WorkflowEventCursor,
    WorkflowEventTrigger,
    WorkflowRun,
)
from packages.workflow.spec import WorkflowSpecError, evaluate_condition


def normalize_event_pattern(value: str) -> str:
    pattern = str(value or "").strip().lower()
    if not pattern or len(pattern) > 160:
        raise ValueError("Event pattern is required and must be at most 160 characters")
    if pattern == "*":
        return pattern
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        if not prefix or any(not part for part in prefix.split(".")):
            raise ValueError("Event wildcard pattern is invalid")
        pattern = prefix + ".*"
    elif "*" in pattern:
        raise ValueError("Only a trailing .* wildcard is supported")
    if ".." in pattern or pattern.startswith(".") or pattern.endswith("."):
        raise ValueError("Event pattern is invalid")
    # Workflow lifecycle events are orchestration evidence, not automation triggers.
    # Keeping them off the trigger plane prevents accidental self-trigger loops.
    if pattern == "workflow.*" or pattern.startswith("workflow."):
        raise ValueError("workflow.* lifecycle events cannot be workflow triggers")
    return pattern


def event_matches_pattern(event_type: str, pattern: str) -> bool:
    event_type = str(event_type or "").strip().lower()
    pattern = normalize_event_pattern(pattern)
    if event_type.startswith("workflow."):
        return False
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return event_type.startswith(pattern[:-1])
    return event_type == pattern


def event_envelope(row: KernelEventRecord) -> dict[str, Any]:
    try:
        payload = json.loads(row.payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "id": row.id,
        "type": row.event_type,
        "scope_kind": row.scope_kind,
        "workspace_id": row.workspace_id,
        "owner_user_id": row.owner_user_id,
        "principal_id": row.principal_id,
        "actor_type": row.actor_type,
        "actor_id": row.actor_id,
        "initiator_principal_id": row.initiator_principal_id,
        "executor_principal_id": row.executor_principal_id,
        "capability_id": row.capability_id,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "payload": payload,
        "created_at": row.created_at.isoformat(),
        "correlation_id": payload.get("workflow_correlation_id") or row.id,
        "causation_id": payload.get("workflow_causation_id"),
    }


def _condition_matches(raw: str, envelope: dict[str, Any]) -> bool:
    try:
        condition = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return False
    if not condition:
        return True
    if not isinstance(condition, dict):
        return False
    try:
        return evaluate_condition(condition, {"event": envelope})
    except WorkflowSpecError:
        return False


class WorkflowEventDispatcher:
    """Durable Kernel-event -> WorkflowRun fanout.

    A single cursor row is locked while a batch is converted into runs. Cursor advance
    and run enqueue are committed atomically, while each run also carries a unique
    event/trigger dedupe key. Multiple API replicas may therefore run this dispatcher
    without silently losing or duplicating workflow invocations.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_error: str | None = None
        self._last_tick_at: datetime | None = None
        self._poll_seconds = max(
            0.5, float(os.getenv("OPERLY_WORKFLOW_EVENT_POLL_SECONDS", "1"))
        )
        self._batch_size = max(
            1, min(int(os.getenv("OPERLY_WORKFLOW_EVENT_BATCH_SIZE", "100")), 500)
        )
        self._max_depth = max(
            1, min(int(os.getenv("OPERLY_WORKFLOW_EVENT_MAX_DEPTH", "8")), 32)
        )

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self._loop(), name="operly-workflow-event-dispatcher"
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._task and not self._task.done()),
            "poll_seconds": self._poll_seconds,
            "batch_size": self._batch_size,
            "max_depth": self._max_depth,
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

    async def tick(self) -> int:
        self._last_tick_at = datetime.utcnow()
        async with session_scope() as db:
            cursor = await db.scalar(
                select(WorkflowEventCursor)
                .where(WorkflowEventCursor.id == "kernel")
                .with_for_update(skip_locked=True)
            )
            if cursor is None:
                # Migration seeds this row. If another replica currently owns the lock,
                # skip this tick instead of creating a second cursor.
                return 0
            if cursor.last_created_at is None:
                # Defensive compatibility for a partially upgraded database. Start at
                # now rather than replaying the historical Kernel event log.
                cursor.last_created_at = datetime.utcnow()
                cursor.last_event_id = ""
                await db.commit()
                return 0

            events = (
                await db.scalars(
                    select(KernelEventRecord)
                    .where(
                        or_(
                            KernelEventRecord.created_at > cursor.last_created_at,
                            and_(
                                KernelEventRecord.created_at == cursor.last_created_at,
                                KernelEventRecord.id > str(cursor.last_event_id or ""),
                            ),
                        )
                    )
                    .order_by(
                        KernelEventRecord.created_at.asc(), KernelEventRecord.id.asc()
                    )
                    .limit(self._batch_size)
                )
            ).all()
            if not events:
                return 0

            queued = 0
            for event in events:
                envelope = event_envelope(event)
                payload = envelope["payload"]
                depth = max(0, int(payload.get("workflow_depth") or 0))
                if depth < self._max_depth and not event.event_type.startswith("workflow."):
                    trigger_rows = await self._triggers_for_event(db, event)
                    for trigger, workflow in trigger_rows:
                        if not event_matches_pattern(event.event_type, trigger.event_pattern):
                            continue
                        if not _condition_matches(trigger.condition_json, envelope):
                            continue
                        dedupe_key = f"event:{trigger.id}:{event.id}"
                        existing = await db.scalar(
                            select(WorkflowRun.id).where(
                                WorkflowRun.dedupe_key == dedupe_key
                            )
                        )
                        if existing is not None:
                            continue
                        await queue_workflow_run(
                            db,
                            workflow=workflow,
                            trigger_type="event",
                            trigger_payload={
                                "event": envelope,
                                "trigger_id": trigger.id,
                                "depth": depth + 1,
                            },
                            initiated_by_user_id=None,
                            dedupe_key=dedupe_key,
                        )
                        queued += 1

                cursor.last_created_at = event.created_at
                cursor.last_event_id = event.id

            await db.commit()
            return queued

    async def _triggers_for_event(self, db, event: KernelEventRecord):
        filters = [
            WorkflowEventTrigger.enabled.is_(True),
            WorkflowDefinition.status == "enabled",
            WorkflowDefinition.scope_kind == event.scope_kind,
        ]
        if event.scope_kind == "personal":
            if not event.owner_user_id:
                return []
            filters.extend(
                [
                    WorkflowDefinition.workspace_id.is_(None),
                    WorkflowDefinition.owner_user_id == event.owner_user_id,
                ]
            )
        else:
            if not event.workspace_id:
                return []
            filters.append(WorkflowDefinition.workspace_id == event.workspace_id)
        return (
            await db.execute(
                select(WorkflowEventTrigger, WorkflowDefinition)
                .join(
                    WorkflowDefinition,
                    WorkflowDefinition.id == WorkflowEventTrigger.workflow_id,
                )
                .where(*filters)
                .order_by(WorkflowEventTrigger.created_at.asc())
            )
        ).all()


workflow_event_dispatcher = WorkflowEventDispatcher()

__all__ = [
    "WorkflowEventDispatcher",
    "event_envelope",
    "event_matches_pattern",
    "normalize_event_pattern",
    "workflow_event_dispatcher",
]