import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.actions.service import ActionService
from packages.business.service import BusinessService
from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.defaults import bootstrap_builtin_plugins
from packages.capabilities.event_provider import EventDiscoveryProvider
from packages.capabilities.providers import BaseProvider
from packages.capabilities.registry import CapabilityRegistry
from packages.capabilities.registry_workflow_task_provider import _invoke_capabilities
from packages.capabilities.task_provider import dump_task_payload, load_task_payload
from packages.database import principal_models as _principal_models  # noqa: F401
from packages.database.company_models import BusinessEventRecord
from packages.database.db import Base
from packages.database.models import AppUser, ScheduledJob, Task, Tenant
from packages.plugins import default_plugin_runtime
from packages.tasks.workflow import validate_workflow


class _MutationProvider(BaseProvider):
    name = "test_mutation"
    capabilities = (
        CapabilityDefinition(
            "test.change",
            "test_change",
            "Mutate a test resource through the same governed capability runtime.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="low",
            permissions=(),
            approval_policy=ApprovalPolicy.AUTO,
            category="test",
        ),
    )

    async def execute(self, context, capability_name, arguments):
        del context, capability_name, arguments
        return CapabilityResult(True, True, {"resource_id": "resource-1"}, "resource-1")

    async def verify(self, context, capability_name, arguments, result):
        del context, capability_name, arguments
        return CapabilityResult(
            result.success,
            result.changed,
            {"verified": result.success, "resource_id": result.external_reference},
            result.external_reference,
        )


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_crm_contact_created_event_is_plugin_owned_and_discoverable():
    async def scenario():
        bootstrap_builtin_plugins()
        manifests = default_plugin_runtime().manifests
        assert manifests.owner_for_event("crm.contact.created") == "builtin:operly_business"
        event = manifests.event("crm.contact.created")
        assert event.scope == "workspace"
        assert event.payload_schema["required"] == ["contact_id", "name", "source"]

        provider = EventDiscoveryProvider()
        result = await provider.execute(
            SimpleNamespace(),
            "event.search",
            {"query": "new CRM contact", "scope": "workspace", "limit": 50},
        )
        assert result.success
        rows = [
            row
            for row in result.evidence["events"]
            if row["id"] == "crm.contact.created"
        ]
        assert len(rows) == 1
        assert rows[0]["plugin_id"] == "builtin:operly_business"

    asyncio.run(scenario())


def test_capability_lifecycle_is_a_discoverable_realtime_trigger_surface():
    async def scenario():
        bootstrap_builtin_plugins()
        manifests = default_plugin_runtime().manifests
        assert manifests.owner_for_event("action.verified") == "builtin:capability_runtime"
        assert manifests.owner_for_event("action.failed") == "builtin:capability_runtime"

        event = manifests.event("action.verified")
        assert event.scope == "workspace"
        assert event.payload_schema["required"] == ["action_id", "capability", "status"]
        assert "realtime" in event.tags

        provider = EventDiscoveryProvider()
        result = await provider.execute(
            SimpleNamespace(),
            "event.search",
            {"query": "verified capability", "scope": "workspace", "limit": 50},
        )
        assert result.success
        rows = [row for row in result.evidence["events"] if row["id"] == "action.verified"]
        assert len(rows) == 1
        assert rows[0]["plugin_id"] == "builtin:capability_runtime"

    asyncio.run(scenario())


def test_create_contact_emits_event_and_wakes_matching_workflow_task():
    async def scenario():
        bootstrap_builtin_plugins()
        engine, Session = await _database()
        try:
            async with Session() as db:
                tenant = Tenant(name="CRM Event Workspace")
                user = AppUser(
                    email=f"crm-events-{uuid4()}@example.com",
                    display_name="CRM Event Owner",
                )
                db.add_all([tenant, user])
                await db.flush()

                task = Task(
                    tenant_id=tenant.id,
                    owner_user_id=user.id,
                    title="Notify about website CRM contacts",
                    status="open",
                )
                db.add(task)
                await db.flush()
                payload = {
                    "version": 3,
                    "objective": "Notify me when a website contact is created.",
                    "trigger": {
                        "kind": "event",
                        "event_id": "crm.contact.created",
                        "where": {"payload.source": "website"},
                    },
                    "workflow": {
                        "steps": [
                            {
                                "id": "out",
                                "type": "emit",
                                "value": "$trigger.payload.name",
                            }
                        ]
                    },
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

                contact = await BusinessService.create_contact(
                    db,
                    tenant.id,
                    name="Ada Lovelace",
                    email="ada@example.com",
                    company="Analytical Engines",
                    source="website",
                    actor=user.id,
                )
                await db.flush()

                event_row = await db.scalar(
                    select(BusinessEventRecord).where(
                        BusinessEventRecord.tenant_id == tenant.id,
                        BusinessEventRecord.event_type == "crm.contact.created",
                    )
                )
                assert event_row is not None
                event_payload = json.loads(event_row.payload_json)
                assert event_payload == {
                    "company": "Analytical Engines",
                    "contact_id": contact.id,
                    "email": "ada@example.com",
                    "name": "Ada Lovelace",
                    "source": "website",
                }
                assert event_row.source == "plugin:builtin:operly_business"
                assert json.loads(event_row.metadata_json)["plugin_id"] == "builtin:operly_business"

                assert job.status == "pending"
                woke_payload = load_task_payload(job.content)
                event_context = woke_payload["event_context"]
                assert event_context["event_type"] == "crm.contact.created"
                assert event_context["payload"]["contact_id"] == contact.id
                assert event_context["payload"]["name"] == "Ada Lovelace"
                assert event_context["payload"]["source"] == "website"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_verified_capability_wakes_workflow_that_mixes_studio_and_internal_plugins():
    async def scenario():
        bootstrap_builtin_plugins()
        manifests = default_plugin_runtime().manifests

        workflow = validate_workflow(
            {
                "steps": [
                    {
                        "id": "inspect_solution",
                        "type": "invoke",
                        "capability": "studio.public_url",
                        "arguments": {"project_id": "$trigger.payload.action_id"},
                    },
                    {
                        "id": "notify",
                        "type": "invoke",
                        "capability": "discord.send_dm",
                        "arguments": {"message": "$inspect_solution.public_url"},
                    },
                ]
            }
        )
        capabilities = _invoke_capabilities(workflow)
        assert capabilities == {"studio.public_url", "discord.send_dm"}
        assert all(manifests.owner_for_capability(item) is not None for item in capabilities)

        engine, Session = await _database()
        try:
            async with Session() as db:
                tenant = Tenant(name="Generic Workflow Workspace")
                user = AppUser(
                    email=f"generic-workflow-{uuid4()}@example.com",
                    display_name="Workflow Owner",
                )
                db.add_all([tenant, user])
                await db.flush()

                task = Task(
                    tenant_id=tenant.id,
                    owner_user_id=user.id,
                    title="React to any verified test capability",
                    status="open",
                )
                db.add(task)
                await db.flush()
                payload = {
                    "version": 3,
                    "objective": "Run a mixed Studio/plugin workflow in real time.",
                    "trigger": {
                        "kind": "event",
                        "event_id": "action.verified",
                        "where": {"payload.capability": "test.change"},
                    },
                    "workflow": workflow,
                    "state": {},
                    "event_queue": [],
                }
                job = ScheduledJob(
                    tenant_id=tenant.id,
                    task_id=task.id,
                    guild_id=0,
                    channel_id=0,
                    user_id=0,
                    job_type="task",
                    content=dump_task_payload(payload),
                    delivery="origin",
                    run_at=datetime.utcnow(),
                    status="waiting_event",
                )
                db.add(job)
                await db.flush()

                registry = CapabilityRegistry()
                registry.register(_MutationProvider())
                action = await ActionService(db, registry, actor_id=user.id).propose(
                    tenant_id=tenant.id,
                    objective="Change the test resource",
                    capability="test.change",
                    arguments={},
                    rationale="Exercise the generic capability event fabric",
                    expected_outcome="A verified mutation event",
                    risk_level="low",
                )
                await db.flush()

                assert str(action.status) == "VERIFIED"
                verified_event = await db.scalar(
                    select(BusinessEventRecord).where(
                        BusinessEventRecord.tenant_id == tenant.id,
                        BusinessEventRecord.event_type == "action.verified",
                    )
                )
                assert verified_event is not None
                verified_payload = json.loads(verified_event.payload_json)
                assert verified_payload["capability"] == "test.change"
                assert verified_payload["action_id"] == action.id

                assert job.status == "pending"
                woke_payload = load_task_payload(job.content)
                trigger = woke_payload["event_context"]
                assert trigger["event_type"] == "action.verified"
                assert trigger["payload"]["capability"] == "test.change"
                assert woke_payload["workflow"] == workflow
        finally:
            await engine.dispose()

    asyncio.run(scenario())
