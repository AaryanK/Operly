import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.capabilities.task_provider import load_task_payload
from packages.capabilities.universal_task_provider import UniversalTaskProvider
from packages.database.channel_models import ExternalIdentity
from packages.database.db import Base
from packages.database.models import AppUser, ScheduledJob, Task, Tenant
from packages.database.schema import import_all_models
from packages.plugins import EventSpec, PluginContribution, PluginManifest, default_plugin_runtime
from packages.plugins.runtime import PluginRuntime
from packages.tasks.delivery import capture_task_origin, delivery_target_from_origin
from packages.tasks.runtime import resume_task_after_approval


import_all_models()


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _context(db, *, tenant_id, user_id, personal=False, channel="web", conversation_id="conv-1"):
    return SimpleNamespace(
        tenant_id=tenant_id,
        actor_id=user_id,
        db=db,
        invocation={
            "channel": channel,
            "temporal_context": {
                "actor_timezone": "America/Chicago",
                "workspace_timezone": "UTC",
            },
            "metadata": {
                "personal_scope": personal,
                "is_direct": personal,
                "actor_name": "Task User",
                "conversation_id": conversation_id,
                "external_conversation_id": conversation_id,
                "_conversation_id": conversation_id,
                "temporal_context": {
                    "actor_timezone": "America/Chicago",
                    "workspace_timezone": "UTC",
                },
            },
        },
    )


def test_future_plugin_can_contribute_task_delivery_without_task_engine_changes():
    class FutureChatAdapter:
        providers = ("futurechat",)

        async def deliver(self, target, message):
            self.last = (target, message)

    adapter = FutureChatAdapter()
    runtime = PluginRuntime()
    runtime.register(
        PluginContribution(
            manifest=PluginManifest(id="test:futurechat", version="1.0.0"),
            task_delivery_adapters=(adapter,),
        )
    )
    assert runtime.task_delivery_adapter("futurechat") is adapter
    assert runtime.task_delivery_adapter("missing") is None


def test_delivery_target_is_provider_neutral():
    web = delivery_target_from_origin(
        {
            "provider": "web",
            "scope": "workspace",
            "tenant_id": "t1",
            "user_id": "u1",
            "external_conversation_id": "conversation-1",
            "is_direct": False,
        },
        "origin",
    )
    assert web["provider"] == "web"
    assert web["kind"] == "channel"
    assert web["external_conversation_id"] == "conversation-1"

    future = delivery_target_from_origin(
        {
            "provider": "futurechat",
            "scope": "personal",
            "user_id": "u1",
            "external_user_id": "external-u1",
            "external_conversation_id": "room-7",
            "is_direct": True,
        },
        "origin",
    )
    assert future["provider"] == "futurechat"
    assert future["kind"] == "dm"


def test_web_workspace_and_personal_tasks_do_not_require_discord():
    async def scenario():
        engine, Session = await _database()
        provider = UniversalTaskProvider()
        try:
            async with Session() as db:
                user = AppUser(email=f"channel-{uuid4()}@example.com", display_name="Task User")
                tenant = Tenant(name="Channel Neutral Workspace")
                db.add_all([user, tenant])
                await db.flush()
                run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

                workspace = _context(
                    db,
                    tenant_id=tenant.id,
                    user_id=user.id,
                    personal=False,
                    channel="web",
                    conversation_id="workspace-web-conversation",
                )
                created = await provider.execute(
                    workspace,
                    "task.create",
                    {
                        "title": "Web task",
                        "objective": "Return a short status update.",
                        "trigger": {"kind": "once", "run_at": run_at},
                        "workflow": {
                            "steps": [{"id": "out", "type": "emit", "value": "done"}]
                        },
                        "delivery": "origin",
                    },
                )
                assert created.success
                task = await db.get(Task, created.external_reference)
                job = await db.scalar(select(ScheduledJob).where(ScheduledJob.task_id == task.id))
                payload = load_task_payload(job.content)
                assert task.tenant_id == tenant.id
                assert task.guild_id is None and task.channel_id is None and task.creator_id is None
                assert job.channel_id == 0 and job.user_id == 0  # legacy compatibility only
                assert payload["origin"]["provider"] == "web"
                assert payload["delivery_target"]["provider"] == "web"
                assert payload["delivery_target"]["external_conversation_id"] == "workspace-web-conversation"

                personal = _context(
                    db,
                    tenant_id=tenant.id,  # selected workspace must not own the personal Task
                    user_id=user.id,
                    personal=True,
                    channel="web",
                    conversation_id="personal-web-conversation",
                )
                private = await provider.execute(
                    personal,
                    "task.create",
                    {
                        "title": "Private web task",
                        "objective": "Return a private update.",
                        "trigger": {"kind": "once", "run_at": run_at},
                        "delivery": "origin",
                    },
                )
                assert private.success
                private_task = await db.get(Task, private.external_reference)
                private_job = await db.scalar(
                    select(ScheduledJob).where(ScheduledJob.task_id == private_task.id)
                )
                private_payload = load_task_payload(private_job.content)
                assert private_task.tenant_id is None
                assert private_task.owner_user_id == user.id
                assert private_payload["origin"]["scope"] == "personal"
                assert private_payload["delivery_target"]["provider"] == "web"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_workspace_event_task_from_web_needs_no_discord_origin():
    async def scenario():
        engine, Session = await _database()
        provider = UniversalTaskProvider()
        event_id = f"customer.created.{uuid4().hex}"
        plugin_id = f"test:event-source:{uuid4().hex}"
        default_plugin_runtime().register(
            PluginContribution(
                manifest=PluginManifest(
                    id=plugin_id,
                    version="1.0.0",
                    events=(EventSpec(event_id, scope="workspace"),),
                )
            )
        )
        try:
            async with Session() as db:
                user = AppUser(email=f"event-web-{uuid4()}@example.com", display_name="Task User")
                tenant = Tenant(name="Event Workspace")
                db.add_all([user, tenant])
                await db.flush()
                context = _context(
                    db,
                    tenant_id=tenant.id,
                    user_id=user.id,
                    channel="web",
                    conversation_id="web-event-conversation",
                )
                created = await provider.execute(
                    context,
                    "task.create",
                    {
                        "title": "Handle customer",
                        "objective": "Handle a new customer.",
                        "trigger": {"kind": "event", "event_id": event_id},
                        "workflow": {
                            "steps": [{"id": "out", "type": "emit", "value": "handled"}]
                        },
                    },
                )
                assert created.success
                job = await db.scalar(
                    select(ScheduledJob).where(ScheduledJob.task_id == created.external_reference)
                )
                payload = load_task_payload(job.content)
                assert job.status == "waiting_event"
                assert payload["trigger"]["event_id"] == event_id
                assert payload["delivery_target"]["provider"] == "web"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_discord_personal_origin_resolves_linked_identity_without_discord_task_logic():
    async def scenario():
        engine, Session = await _database()
        try:
            async with Session() as db:
                user = AppUser(email=f"discord-origin-{uuid4()}@example.com", display_name="Task User")
                db.add(user)
                await db.flush()
                db.add(
                    ExternalIdentity(
                        user_id=user.id,
                        provider="discord",
                        provider_subject="424242",
                        display_name="Discord User",
                    )
                )
                await db.flush()
                context = _context(
                    db,
                    tenant_id=None,
                    user_id=user.id,
                    personal=True,
                    channel="discord",
                    conversation_id="discord:777",
                )
                context.invocation["metadata"].pop("external_conversation_id", None)
                origin = await capture_task_origin(context)
                assert origin["provider"] == "discord"
                assert origin["external_user_id"] == "424242"
                assert origin["external_conversation_id"] == "777"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_approval_resume_is_channel_agnostic():
    async def scenario():
        engine, Session = await _database()
        try:
            async with Session() as db:
                user = AppUser(email=f"approval-{uuid4()}@example.com", display_name="Task User")
                tenant = Tenant(name="Approval Workspace")
                db.add_all([user, tenant])
                await db.flush()
                task = Task(
                    tenant_id=tenant.id,
                    owner_user_id=user.id,
                    title="Approval task",
                    status="open",
                )
                db.add(task)
                await db.flush()
                payload = {
                    "version": 3,
                    "objective": "Do approved work.",
                    "trigger": {"kind": "once"},
                    "origin": {"provider": "web", "scope": "workspace"},
                    "delivery_target": {"provider": "web", "scope": "workspace"},
                    "waiting_approval": "approval-123",
                    "active_run_key": "run-123",
                }
                job = ScheduledJob(
                    tenant_id=tenant.id,
                    task_id=task.id,
                    guild_id=None,
                    channel_id=0,
                    user_id=0,
                    job_type="task",
                    content=__import__("json").dumps(payload),
                    delivery="origin",
                    run_at=datetime.utcnow(),
                    status="waiting_approval",
                )
                db.add(job)
                await db.flush()
                changed = await resume_task_after_approval(
                    db,
                    "approval-123",
                    approved=True,
                )
                assert changed == 1
                assert job.status == "pending"
                resumed_payload = load_task_payload(job.content)
                assert "waiting_approval" not in resumed_payload
                assert resumed_payload["active_run_key"] == "run-123"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
