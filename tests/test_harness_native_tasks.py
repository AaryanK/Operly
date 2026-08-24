import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.capabilities.defaults import _builtin_providers
from packages.capabilities.task_provider import (
    TASK_NO_CHANGE,
    TaskProvider,
    load_task_payload,
    next_task_run,
    scheduled_task_prompt,
)
from packages.database.channel_models import ExternalIdentity
from packages.database.db import Base
from packages.database.models import AppUser, ScheduledJob, Task, Tenant
from packages.database.schema import ALEMBIC_HEAD


def _context(db, *, tenant_id, user_id, personal=False, direct=False):
    return SimpleNamespace(
        tenant_id=tenant_id,
        actor_id=user_id,
        db=db,
        invocation={
            "channel": "discord",
            "temporal_context": {"actor_timezone": "America/Chicago"},
            "metadata": {
                "personal_scope": personal,
                "is_direct": direct,
                "external_user_id": "424242" if not personal else None,
                "external_space_id": None if direct else "999",
                "external_conversation_id": "777",
                "discord_guild_id": None if direct else 999,
                "discord_channel_id": 777,
                "discord_user_id": 424242,
                "actor_name": "Tester",
            },
        },
    )


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_task_and_web_are_builtin_plugins():
    ids = {
        definition.id
        for provider in _builtin_providers()
        for definition in provider.capabilities
    }
    assert "task.create" in ids
    assert "task.list" in ids
    assert "task.update" in ids
    assert "task.check_url_change" in ids
    assert "web.read_url" in ids
    assert ALEMBIC_HEAD == "0037_harness_native_tasks"


def test_recurring_schedule_helpers_and_monitor_prompt():
    previous = datetime(2026, 8, 24, 1, 0, 0)
    interval = next_task_run(
        {"trigger": {"kind": "interval", "every_minutes": 30}},
        previous,
    )
    assert interval == previous + timedelta(minutes=30)

    daily = next_task_run(
        {
            "trigger": {
                "kind": "daily",
                "timezone": "America/Chicago",
                "local_time": "20:00:00",
            }
        },
        previous,
    )
    assert daily is not None
    assert daily > previous

    fake_task = SimpleNamespace(id="task-1", title="Blog monitor")
    prompt = scheduled_task_prompt(
        fake_task,
        {
            "objective": "Summarize the new post and send it here.",
            "trigger": {"kind": "monitor", "url": "https://example.blogspot.com/"},
        },
    )
    assert "task.check_url_change" in prompt
    assert TASK_NO_CHANGE in prompt
    assert "Do not create, reschedule, edit, or duplicate this task" in prompt


def test_workspace_and_personal_task_creation_and_crud():
    async def scenario():
        engine, Session = await _database()
        provider = TaskProvider()
        try:
            async with Session() as db:
                user = AppUser(email="task-user@example.com", display_name="Task User")
                tenant = Tenant(name="Task Workspace")
                db.add_all([user, tenant])
                await db.flush()
                db.add(
                    ExternalIdentity(
                        user_id=user.id,
                        provider="discord",
                        provider_subject="424242",
                        display_name="Task User",
                    )
                )
                await db.flush()

                run_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
                workspace = _context(
                    db,
                    tenant_id=tenant.id,
                    user_id=user.id,
                    personal=False,
                    direct=False,
                )
                created = await provider.execute(
                    workspace,
                    "task.create",
                    {
                        "title": "Nightly story",
                        "objective": "Write a new short bedtime story and send it here.",
                        "trigger": {"kind": "daily", "run_at": run_at},
                        "delivery": "origin",
                    },
                )
                assert created.success
                task_id = created.external_reference
                task = await db.get(Task, task_id)
                job = await db.scalar(select(ScheduledJob).where(ScheduledJob.task_id == task_id))
                assert task is not None and task.tenant_id == tenant.id
                assert task.owner_user_id == user.id
                assert job is not None and job.job_type == "task"
                assert job.delivery == "channel"
                assert load_task_payload(job.content)["trigger"]["kind"] == "daily"
                original_job_id = job.id

                listing = await provider.execute(workspace, "task.list", {})
                assert listing.success and listing.evidence["count"] == 1

                updated = await provider.execute(
                    workspace,
                    "task.update",
                    {
                        "task_id": task_id,
                        "objective": "Write a funny bedtime story and send it here.",
                        "status": "paused",
                    },
                )
                assert updated.success
                assert task.status == "paused"
                assert job.status == "paused"

                resumed = await provider.execute(
                    workspace,
                    "task.update",
                    {"task_id": task_id, "status": "open"},
                )
                assert resumed.success
                resumed_job = await db.scalar(
                    select(ScheduledJob).where(ScheduledJob.task_id == task_id)
                )
                assert resumed_job is not None
                assert resumed_job.status == "pending"
                assert resumed_job.id != original_job_id
                assert load_task_payload(resumed_job.content)["objective"].startswith("Write a funny")

                rescheduled_for = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
                rescheduled = await provider.execute(
                    workspace,
                    "task.update",
                    {
                        "task_id": task_id,
                        "trigger": {"kind": "daily", "run_at": rescheduled_for},
                    },
                )
                assert rescheduled.success
                rescheduled_job = await db.scalar(
                    select(ScheduledJob).where(ScheduledJob.task_id == task_id)
                )
                assert rescheduled_job is not None
                assert rescheduled_job.id != resumed_job.id
                assert rescheduled_job.run_at > resumed_job.run_at

                personal = _context(
                    db,
                    tenant_id=tenant.id,
                    user_id=user.id,
                    personal=True,
                    direct=True,
                )
                personal.invocation["metadata"]["external_user_id"] = None
                personal_created = await provider.execute(
                    personal,
                    "task.create",
                    {
                        "title": "Private story",
                        "objective": "Write a private bedtime story and DM it to me.",
                        "trigger": {"kind": "daily", "run_at": run_at},
                        "delivery": "origin",
                    },
                )
                assert personal_created.success
                personal_task = await db.get(Task, personal_created.external_reference)
                personal_job = await db.scalar(
                    select(ScheduledJob).where(ScheduledJob.task_id == personal_task.id)
                )
                assert personal_task.tenant_id is None
                assert personal_task.owner_user_id == user.id
                assert personal_job.delivery == "dm"

                private_listing = await provider.execute(personal, "task.list", {})
                assert private_listing.success
                assert private_listing.evidence["count"] == 1
                assert private_listing.evidence["tasks"][0]["id"] == personal_task.id

                cancelled = await provider.execute(
                    personal,
                    "task.cancel",
                    {"task_id": personal_task.id},
                )
                assert cancelled.success
                assert personal_task.status == "cancelled"
                assert personal_job.status == "cancelled"
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_monitor_establishes_baseline_then_reports_change():
    async def scenario():
        engine, Session = await _database()
        provider = TaskProvider()
        try:
            async with Session() as db:
                user = AppUser(email="monitor-user@example.com", display_name="Monitor User")
                tenant = Tenant(name="Monitor Workspace")
                db.add_all([user, tenant])
                await db.flush()
                run_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
                context = _context(
                    db,
                    tenant_id=tenant.id,
                    user_id=user.id,
                    personal=False,
                    direct=False,
                )
                created = await provider.execute(
                    context,
                    "task.create",
                    {
                        "title": "Blogspot watch",
                        "objective": "When the blog changes, summarize the update and post it here.",
                        "trigger": {
                            "kind": "monitor",
                            "run_at": run_at,
                            "every_minutes": 15,
                            "url": "https://example.blogspot.com/",
                        },
                    },
                )
                assert created.success
                task_id = created.external_reference

                first_fetch = {
                    "final_url": "https://example.blogspot.com/",
                    "sha256": "a" * 64,
                    "text": "First version",
                }
                second_fetch = {
                    "final_url": "https://example.blogspot.com/",
                    "sha256": "b" * 64,
                    "text": "Second version",
                }
                with patch(
                    "packages.capabilities.task_provider.fetch_public_text",
                    new=AsyncMock(side_effect=[first_fetch, second_fetch]),
                ):
                    baseline = await provider.execute(
                        context,
                        "task.check_url_change",
                        {"task_id": task_id},
                    )
                    assert baseline.success
                    assert baseline.evidence["first_observation"] is True
                    assert baseline.evidence["content_changed"] is False
                    assert baseline.evidence["text"] is None

                    changed = await provider.execute(
                        context,
                        "task.check_url_change",
                        {"task_id": task_id},
                    )
                    assert changed.success
                    assert changed.evidence["first_observation"] is False
                    assert changed.evidence["content_changed"] is True
                    assert changed.evidence["text"] == "Second version"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
