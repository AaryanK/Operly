import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.capabilities.task_provider import load_task_payload
from packages.database.db import Base
from packages.database.models import AppUser, ScheduledJob, Task, Tenant
from packages.database.schema import import_all_models
from packages.tasks.events import wake_workspace_tasks
from packages.tasks.runtime import _preserve_live_event_queue, _recover_stale_running


import_all_models()


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_runtime_state_write_preserves_concurrent_event_queue():
    job = SimpleNamespace(
        content=json.dumps(
            {
                "event_queue": [
                    {"id": "event-2", "event_type": "customer.created"},
                    {"id": "event-3", "event_type": "customer.created"},
                ]
            }
        )
    )
    local_payload = {
        "event_queue": [],
        "state": {"processed": "event-1"},
    }
    _preserve_live_event_queue(job, local_payload)
    assert [item["id"] for item in local_payload["event_queue"]] == ["event-2", "event-3"]
    assert local_payload["state"]["processed"] == "event-1"


def test_event_arriving_during_approval_wait_is_queued_not_promoted():
    async def scenario():
        engine, Session = await _database()
        try:
            async with Session() as db:
                tenant = Tenant(name="Event Queue Workspace")
                user = AppUser(email=f"event-queue-{uuid4()}@example.com", display_name="Task User")
                db.add_all([tenant, user])
                await db.flush()
                task = Task(
                    tenant_id=tenant.id,
                    owner_user_id=user.id,
                    title="Customer workflow",
                    status="open",
                )
                db.add(task)
                await db.flush()
                current_event = {"id": "event-current", "event_type": "customer.created"}
                job = ScheduledJob(
                    tenant_id=tenant.id,
                    task_id=task.id,
                    guild_id=None,
                    channel_id=0,
                    user_id=0,
                    job_type="task",
                    content=json.dumps(
                        {
                            "trigger": {"kind": "event", "event_id": "customer.created"},
                            "event_context": current_event,
                            "event_queue": [],
                            "waiting_approval": "approval-1",
                        }
                    ),
                    delivery="origin",
                    run_at=datetime.utcnow(),
                    status="waiting_approval",
                )
                db.add(job)
                await db.flush()
                event = SimpleNamespace(
                    id="event-next",
                    tenant_id=tenant.id,
                    event_type="customer.created",
                    occurred_at=datetime.utcnow(),
                    actor_type="customer",
                    actor_id="customer-2",
                    source="future-plugin",
                    payload={"customer_id": "customer-2"},
                    correlation_id=None,
                    causation_id=None,
                    metadata={},
                )
                assert await wake_workspace_tasks(db, event) == 1
                assert job.status == "waiting_approval"
                payload = load_task_payload(job.content)
                assert payload["event_context"]["id"] == "event-current"
                assert [item["id"] for item in payload["event_queue"]] == ["event-next"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_stale_running_task_is_recovered_with_same_run_identity():
    async def scenario():
        engine, Session = await _database()
        try:
            async with Session() as db:
                tenant = Tenant(name="Recovery Workspace")
                user = AppUser(email=f"recovery-{uuid4()}@example.com", display_name="Task User")
                db.add_all([tenant, user])
                await db.flush()
                task = Task(
                    tenant_id=tenant.id,
                    owner_user_id=user.id,
                    title="Recover task",
                    status="open",
                )
                db.add(task)
                await db.flush()
                now = datetime(2026, 8, 24, 2, 0, 0)
                payload = {
                    "trigger": {"kind": "interval", "every_minutes": 60},
                    "active_run_key": "same-run",
                    "active_scheduled_for": "2026-08-24T00:00:00",
                    "run_started_at": (now - timedelta(hours=1)).isoformat() + "Z",
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
                    run_at=now - timedelta(hours=1),
                    status="running",
                )
                db.add(job)
                await db.flush()
                assert await _recover_stale_running(db, now) == 1
                assert job.status == "pending"
                assert job.run_at == now
                recovered = load_task_payload(job.content)
                assert recovered["active_run_key"] == "same-run"
                assert recovered["active_scheduled_for"] == "2026-08-24T00:00:00"
                assert "run_started_at" not in recovered
        finally:
            await engine.dispose()

    asyncio.run(scenario())
