import asyncio
import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.capabilities.task_provider import load_task_payload
from packages.database.db import Base
from packages.database.models import AppUser, ScheduledJob, Task, Tenant
from packages.database.schema import import_all_models
from packages.tasks.runtime import resume_task_after_approval


import_all_models()


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_rejected_recurring_approval_keeps_original_wall_clock_schedule():
    async def scenario():
        engine, Session = await _database()
        try:
            async with Session() as db:
                user = AppUser(email=f"schedule-{uuid4()}@example.com", display_name="Task User")
                tenant = Tenant(name="Schedule Workspace")
                db.add_all([user, tenant])
                await db.flush()
                task = Task(
                    tenant_id=tenant.id,
                    owner_user_id=user.id,
                    title="Daily Kathmandu task",
                    status="open",
                )
                db.add(task)
                await db.flush()
                payload = {
                    "version": 3,
                    "objective": "Do the daily work.",
                    "trigger": {
                        "kind": "daily",
                        "timezone": "Asia/Kathmandu",
                        "local_time": "20:00:00",
                    },
                    "waiting_approval": "approval-daily",
                    "active_run_key": "run-daily",
                    # 20:00 in Nepal on Aug 24, 2026 = 14:15 UTC.
                    "active_scheduled_for": "2026-08-24T14:15:00",
                }
                job = ScheduledJob(
                    tenant_id=tenant.id,
                    task_id=task.id,
                    guild_id=None,
                    channel_id=0,
                    user_id=0,
                    job_type="task",
                    content=json.dumps(payload),
                    delivery="origin",
                    # Deliberately unrelated to the original schedule: approval time
                    # must never become the recurrence anchor.
                    run_at=datetime(2026, 8, 24, 23, 30, 0),
                    status="waiting_approval",
                )
                db.add(job)
                await db.flush()

                changed = await resume_task_after_approval(
                    db,
                    "approval-daily",
                    approved=False,
                    tenant_id=tenant.id,
                )
                assert changed == 1
                assert job.status == "pending"
                assert job.run_at == datetime(2026, 8, 25, 14, 15, 0)
                assert task.due_at == job.run_at
                resumed = load_task_payload(job.content)
                assert "active_run_key" not in resumed
                assert "active_scheduled_for" not in resumed
                assert resumed["last_approval_result"]["approved"] is False
        finally:
            await engine.dispose()

    asyncio.run(scenario())
