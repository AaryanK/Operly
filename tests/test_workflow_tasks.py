import asyncio
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.capabilities.agent_harness import PluginInvocationContext
from packages.capabilities.event_provider import EventDiscoveryProvider
from packages.capabilities.task_provider import dump_task_payload, load_task_payload
from packages.capabilities.workflow_task_provider import WorkflowTaskProvider
from packages.company.events.service import BusinessEvent
from packages.database import principal_models as _principal_models  # noqa: F401
from packages.database.db import Base
from packages.database.models import AppUser, ScheduledJob, Task, Tenant
from packages.plugins import EventSpec, PluginContribution, PluginManifest, default_plugin_runtime
from packages.tasks.events import wake_workspace_tasks
from packages.tasks.safe_workflow import ApprovalAwareWorkflowExecutor
from packages.tasks.workflow import WorkflowExecutionError, WorkflowExecutor, WorkflowValidationError, validate_workflow


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _register_event(event_id: str):
    plugin_id = f"test:workflow-events:{uuid4()}"
    default_plugin_runtime().register(
        PluginContribution(
            manifest=PluginManifest(
                id=plugin_id,
                version="1.0.0",
                events=(
                    EventSpec(
                        event_id,
                        "Synthetic customer event used to prove future plugins join Task discovery without Task-engine edits.",
                        payload_schema={"type": "object", "properties": {"customer_id": {"type": "string"}}},
                        scope="workspace",
                        tags=frozenset({"customer", "test"}),
                    ),
                ),
            )
        )
    )
    return plugin_id


def test_future_plugin_events_are_discoverable_without_task_engine_changes():
    async def scenario():
        event_id = f"customer.created.{uuid4().hex}"
        plugin_id = _register_event(event_id)
        provider = EventDiscoveryProvider()
        result = await provider.execute(
            SimpleNamespace(),
            "event.search",
            {"query": "customer", "scope": "workspace", "limit": 50},
        )
        assert result.success
        rows = [row for row in result.evidence["events"] if row["id"] == event_id]
        assert len(rows) == 1
        assert rows[0]["plugin_id"] == plugin_id
        described = await provider.execute(
            SimpleNamespace(),
            "event.describe",
            {"event_id": event_id},
        )
        assert described.success
        assert described.evidence["event"]["payload_schema"]["type"] == "object"

    asyncio.run(scenario())


def test_workflow_language_is_bounded_but_composable():
    valid = {
        "steps": [
            {"id": "read", "type": "invoke", "capability": "example.read", "args": {}},
            {
                "id": "score",
                "type": "model",
                "capability": "reasoning",
                "objective": "Return JSON deciding whether this matters.",
                "context": {"source": "$read"},
                "parse_json": True,
            },
            {
                "type": "if",
                "condition": {"left": "$score.publish", "op": "eq", "right": True},
                "then": [{"id": "out", "type": "emit", "value": "publish"}],
            },
        ]
    }
    assert validate_workflow(valid)["version"] == 1
    try:
        validate_workflow({"steps": [{"id": "bad", "type": "shell", "command": "rm -rf /"}]})
        assert False, "shell nodes must never be accepted"
    except WorkflowValidationError:
        pass


class _FakeWorkflowExecutor(WorkflowExecutor):
    async def _invoke_workspace(self, capability, args, context, *, call_id):
        if capability == "example.read":
            return {"ok": True, "observation": {"headline": "Important Nepal story"}}
        if capability == "model.invoke":
            assert "Important Nepal story" in args["context"]
            return {"ok": True, "observation": {"content": '{"publish": true, "score": 91}'}}
        raise AssertionError(capability)


def test_workflow_executor_composes_capability_model_and_condition():
    async def scenario():
        workflow = {
            "steps": [
                {"id": "story", "type": "invoke", "capability": "example.read", "args": {}},
                {
                    "id": "score",
                    "type": "model",
                    "capability": "reasoning",
                    "objective": "Will this matter in Nepal? Return JSON.",
                    "context": {"story": "$story"},
                    "parse_json": True,
                },
                {
                    "type": "if",
                    "condition": {"left": "$score.score", "op": "gte", "right": 80},
                    "then": [
                        {"id": "remember", "type": "set", "target": "state.last_score", "value": "$score.score"},
                        {"id": "output", "type": "emit", "value": "$story.headline"},
                    ],
                },
            ]
        }
        result = await _FakeWorkflowExecutor().execute(
            workflow,
            context=PluginInvocationContext(
                tenant_id="tenant",
                user_id="user",
                role="owner",
                objective="test",
            ),
        )
        assert result.output == "Important Nepal story"
        assert result.state["last_score"] == 91

    asyncio.run(scenario())


class _WaitingRegistry:
    def definition(self, capability):
        return SimpleNamespace(id=capability)

    def availability(self, tenant_id, capability, *, authority=None):
        return SimpleNamespace(available=True, as_dict=lambda: {"available": True})


class _WaitingView:
    def expose(self, ids):
        self.ids = ids


class _WaitingHarness:
    async def authority_for(self, context):
        return SimpleNamespace()

    async def registry_for(self, context):
        return _WaitingRegistry()

    def capability_authorized(self, capability_id, authority, context):
        return True

    async def session_view_for(self, context, *, authority, registry):
        return _WaitingView()

    async def invoke(self, capability, args, context, *, call_id=None):
        return {
            "ok": True,
            "status": "WAITING_APPROVAL",
            "approval_id": "approval-123",
            "observation": {"reason": "human approval required"},
        }


def test_workflow_does_not_advance_past_waiting_approval():
    async def scenario():
        executor = ApprovalAwareWorkflowExecutor(harness=_WaitingHarness())
        try:
            await executor.execute(
                {
                    "steps": [
                        {"id": "publish", "type": "invoke", "capability": "social.publish", "args": {"text": "hello"}},
                        {"id": "after", "type": "emit", "value": "should-not-run"},
                    ]
                },
                context=SimpleNamespace(tenant_id="tenant"),
            )
            assert False, "approval-gated action must suspend the workflow"
        except WorkflowExecutionError as error:
            assert "workflow_waiting_approval:approval-123" in str(error)

    asyncio.run(scenario())


def test_workspace_event_wakes_matching_task_and_queues_concurrent_events():
    async def scenario():
        engine, Session = await _database()
        try:
            async with Session() as db:
                tenant = Tenant(name="Event Workspace")
                user = AppUser(email=f"event-{uuid4()}@example.com", display_name="Event Owner")
                db.add_all([tenant, user])
                await db.flush()
                event_id = f"customer.created.{uuid4().hex}"
                task = Task(
                    tenant_id=tenant.id,
                    owner_user_id=user.id,
                    title="Handle customer",
                    status="open",
                )
                db.add(task)
                await db.flush()
                payload = {
                    "version": 2,
                    "objective": "Handle matching customer.",
                    "trigger": {"kind": "event", "event_id": event_id, "where": {"payload.tier": "vip"}},
                    "workflow": {"steps": [{"id": "out", "type": "emit", "value": "ok"}]},
                    "state": {},
                    "event_queue": [],
                }
                job = ScheduledJob(
                    tenant_id=tenant.id,
                    task_id=task.id,
                    guild_id=1,
                    channel_id=2,
                    user_id=3,
                    job_type="task",
                    content=dump_task_payload(payload),
                    delivery="channel",
                    run_at=datetime.utcnow(),
                    status="waiting_event",
                )
                db.add(job)
                await db.flush()
                first = BusinessEvent(
                    id=str(uuid4()), tenant_id=tenant.id, event_type=event_id,
                    occurred_at=datetime.utcnow(), actor_type="customer", actor_id="c1",
                    source="future-plugin", payload={"tier": "vip"},
                    correlation_id=None, causation_id=None, metadata={},
                )
                assert await wake_workspace_tasks(db, first) == 1
                assert job.status == "pending"
                assert load_task_payload(job.content)["event_context"]["actor_id"] == "c1"

                job.status = "running"
                second = BusinessEvent(
                    id=str(uuid4()), tenant_id=tenant.id, event_type=event_id,
                    occurred_at=datetime.utcnow(), actor_type="customer", actor_id="c2",
                    source="future-plugin", payload={"tier": "vip"},
                    correlation_id=None, causation_id=None, metadata={},
                )
                assert await wake_workspace_tasks(db, second) == 1
                queued = load_task_payload(job.content)["event_queue"]
                assert len(queued) == 1 and queued[0]["actor_id"] == "c2"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_daily_trigger_preserves_explicit_iana_timezone_over_actor_default():
    prepared, zone, local_time = WorkflowTaskProvider._prepare_daily_trigger(
        {
            "kind": "daily",
            "timezone": "Europe/Helsinki",
            "local_time": "20:00",
        },
        {"actor_timezone": "Asia/Kathmandu", "workspace_timezone": "UTC"},
    )
    assert zone == "Europe/Helsinki"
    assert local_time == "20:00"
    assert prepared["run_at"].endswith("+03:00") or prepared["run_at"].endswith("+02:00")
