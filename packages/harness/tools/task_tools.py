from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from packages.database.db import session_scope
from packages.database.models import ScheduledJob, Task
from packages.harness.context import ToolContext
from packages.harness.registry import ToolRegistry


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_delay(value: int, unit: str) -> timedelta:
    if value <= 0:
        raise ValueError("value must be positive")

    units = {
        "seconds": timedelta(seconds=value),
        "minutes": timedelta(minutes=value),
        "hours": timedelta(hours=value),
        "days": timedelta(days=value),
    }
    if unit not in units:
        raise ValueError("Unsupported unit")
    return units[unit]


async def create_reminder(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    try:
        value = int(args["value"])
        unit = str(args["unit"]).lower()
        delay = parse_delay(value, unit)
    except (KeyError, TypeError, ValueError) as error:
        return {"ok": False, "error": str(error)}

    content = str(args.get("content", "")).strip()
    delivery = str(args.get("delivery", "channel")).lower()

    if not content:
        return {"ok": False, "error": "content is required"}
    if delivery not in {"channel", "dm"}:
        return {"ok": False, "error": "delivery must be channel or dm"}

    run_at = utcnow() + delay

    async with session_scope() as db:
        job = ScheduledJob(
            tenant_id=context.tenant_id,
            guild_id=context.guild_id,
            channel_id=context.channel_id,
            user_id=context.user_id,
            job_type="reminder",
            content=content,
            delivery=delivery,
            run_at=run_at,
            status="pending",
        )
        db.add(job)
        await db.flush()
        job_id = job.id

    scheduler = getattr(context.bot, "operly_scheduler", None)
    callback = getattr(context.bot, "operly_run_scheduled_job", None)

    if scheduler is None or callback is None:
        return {
            "ok": False,
            "error": "Scheduler is unavailable",
            "job_id": job_id,
        }

    scheduler.add_job(
        callback,
        "date",
        run_date=run_at,
        args=[job_id],
        id=job_id,
        replace_existing=True,
    )

    return {
        "ok": True,
        "job_id": job_id,
        "run_at_utc": run_at.isoformat() + "Z",
        "delivery": delivery,
    }


async def create_task(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    title = str(args.get("title", "")).strip()
    if not title:
        return {"ok": False, "error": "title is required"}

    async with session_scope() as db:
        task = Task(
            tenant_id=context.tenant_id,
            guild_id=context.guild_id,
            channel_id=context.channel_id,
            creator_id=context.user_id,
            title=title,
        )
        db.add(task)
        await db.flush()
        task_id = task.id

    return {"ok": True, "task_id": task_id, "title": title}


async def list_tasks(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    async with session_scope() as db:
        rows = (
            await db.scalars(
                select(Task)
                .where(
                    Task.tenant_id == context.tenant_id,
                    Task.status == "open",
                )
                .order_by(Task.created_at)
                .limit(20)
            )
        ).all()

    return {
        "ok": True,
        "tasks": [{"id": row.id, "title": row.title} for row in rows],
    }


async def complete_task(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    task_id = str(args.get("task_id", "")).strip()
    if not task_id:
        return {"ok": False, "error": "task_id is required"}

    async with session_scope() as db:
        row = await db.scalar(
            select(Task).where(
                Task.tenant_id == context.tenant_id,
                Task.id.like(f"{task_id}%"),
            )
        )
        if row is None:
            return {"ok": False, "error": "Task not found"}
        row.status = "completed"

    return {"ok": True, "task_id": row.id}


def register_task_tools(registry: ToolRegistry) -> None:
    registry.register(
        {
            "type": "function",
            "function": {
                "name": "create_reminder",
                "description": (
                    "Actually schedule a reminder. Use whenever the user asks to be "
                    "reminded after a duration. Never merely promise a reminder."
                ),
                "parameters": {
                    "type": "object",
                    "required": ["value", "unit", "content"],
                    "properties": {
                        "value": {"type": "integer", "minimum": 1},
                        "unit": {
                            "type": "string",
                            "enum": ["seconds", "minutes", "hours", "days"],
                        },
                        "content": {
                            "type": "string",
                            "description": "Reminder message.",
                        },
                        "delivery": {
                            "type": "string",
                            "enum": ["channel", "dm"],
                            "description": "Defaults to channel.",
                        },
                    },
                },
            },
        },
        create_reminder,
    )

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "create_task",
                "description": "Create a task for the current business tenant.",
                "parameters": {
                    "type": "object",
                    "required": ["title"],
                    "properties": {"title": {"type": "string"}},
                },
            },
        },
        create_task,
    )

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "list_tasks",
                "description": "List open tasks for the current business tenant.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        list_tasks,
    )

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "complete_task",
                "description": "Complete a tenant-scoped task by its ID or ID prefix.",
                "parameters": {
                    "type": "object",
                    "required": ["task_id"],
                    "properties": {"task_id": {"type": "string"}},
                },
            },
        },
        complete_task,
    )
