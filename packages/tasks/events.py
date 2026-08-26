from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from packages.capabilities.task_provider import dump_task_payload, load_task_payload
from packages.database.models import ScheduledJob, Task


_BUSY_STATUSES = {"pending", "running", "pending_delivery", "waiting_approval"}


def _value(event: Any, path: str):
    first, _, rest = str(path or "").partition(".")
    if first == "payload":
        current = event.payload
        parts = rest.split(".") if rest else []
    elif first == "metadata":
        current = event.metadata
        parts = rest.split(".") if rest else []
    else:
        if hasattr(event, first):
            current = getattr(event, first)
            parts = rest.split(".") if rest else []
        else:
            current = event.payload
            parts = str(path or "").split(".")
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def event_matches(trigger: dict, event: Any) -> bool:
    if str(trigger.get("kind") or "") != "event":
        return False
    if str(trigger.get("event_id") or "") != str(event.event_type):
        return False
    where = trigger.get("where") if isinstance(trigger.get("where"), dict) else {}
    return all(_value(event, key) == expected for key, expected in where.items())


def event_context(event: Any) -> dict:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        # Legacy actor fields remain available, but actor-sensitive workflows should
        # prefer initiator/executor so “Raju did this” differs from “Raju's Operly
        # executed this on Raju's request”.
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "initiator_type": getattr(event, "initiator_type", event.actor_type),
        "initiator_id": getattr(event, "initiator_id", event.actor_id),
        "executor_type": getattr(event, "executor_type", event.actor_type),
        "executor_id": getattr(event, "executor_id", event.actor_id),
        "delegation_chain": list(getattr(event, "delegation_chain", ()) or ()),
        # Full provenance is directly workflow-addressable, e.g.
        # execution_path.entry.surface == "slack" or
        # execution_path.mediation.mode == "ai".
        "execution_path": dict(getattr(event, "execution_path", {}) or {}),
        "source": event.source,
        "payload": event.payload,
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
        "metadata": event.metadata,
    }


async def wake_workspace_tasks(db, event: Any) -> int:
    """Wake open Tasks subscribed to a durable workspace BusinessEvent.

    A Task keeps one ScheduledJob row as its durable wake-up slot. If an event arrives
    while a run is pending, executing, waiting for approval, or waiting for delivery,
    the bounded queue preserves that event for a later run. Event ingestion never
    changes the state of the active run.
    """
    tasks = (
        await db.scalars(
            select(Task).where(
                Task.tenant_id == event.tenant_id,
                Task.status == "open",
            )
        )
    ).all()
    woken = 0
    for task in tasks:
        job = await db.scalar(
            select(ScheduledJob).where(ScheduledJob.task_id == task.id)
        )
        if job is None or job.status in {"cancelled", "completed", "failed", "paused"}:
            continue
        payload = load_task_payload(job.content)
        trigger = payload.get("trigger") if isinstance(payload.get("trigger"), dict) else {}
        if not event_matches(trigger, event):
            continue
        context = event_context(event)
        if job.status in _BUSY_STATUSES:
            queue = payload.get("event_queue") if isinstance(payload.get("event_queue"), list) else []
            queue.append(context)
            payload["event_queue"] = queue[-20:]
        else:
            payload["event_context"] = context
            job.run_at = datetime.utcnow()
            job.status = "pending"
        job.content = dump_task_payload(payload)
        woken += 1
    await db.flush()
    return woken