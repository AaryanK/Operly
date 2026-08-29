import pytest

from packages.agents.control_plane import FactoryCapabilityIntentResolver
from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition
from packages.capabilities.providers import BaseProvider
from packages.capabilities.registry import CapabilityRegistry


_EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


class _DiscoveryFixtureProvider(BaseProvider):
    name = "factory_discovery_fixture"
    capabilities = (
        CapabilityDefinition(
            "calendar.list_events",
            "calendar_list_events",
            "List Google Calendar events in a time window.",
            _EMPTY_SCHEMA,
            {"type": "object"},
            risk_level="read_only",
            permissions=("calendar:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "gmail.search",
            "gmail_search",
            "Search the connected Gmail mailbox using Gmail search syntax.",
            _EMPTY_SCHEMA,
            {"type": "object"},
            risk_level="read_only",
            permissions=("messaging:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "gmail.read_message",
            "gmail_read_message",
            "Read one Gmail message by ID.",
            _EMPTY_SCHEMA,
            {"type": "object"},
            risk_level="read_only",
            permissions=("messaging:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "gmail.read_thread",
            "gmail_read_thread",
            "Read one Gmail thread by ID.",
            _EMPTY_SCHEMA,
            {"type": "object"},
            risk_level="read_only",
            permissions=("messaging:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "task.create",
            "task_create",
            "Create a task with a title and optional deadline.",
            _EMPTY_SCHEMA,
            {"type": "object"},
            risk_level="low",
            permissions=("tasks:write",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "task.create_followup",
            "task_create_followup",
            "Create a follow-up task.",
            _EMPTY_SCHEMA,
            {"type": "object"},
            risk_level="low",
            permissions=("tasks:write",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
    )


@pytest.mark.asyncio
async def test_compiler_pseudo_intents_resolve_through_real_registry_search():
    registry = CapabilityRegistry()
    registry.register(_DiscoveryFixtureProvider())
    resolver = FactoryCapabilityIntentResolver(
        registry=registry,
        scope_id="workspace-1",
        authority={"calendar:read", "messaging:read", "tasks:write"},
    )

    selected = await resolver(("calendar_read", "email_read", "task_create"))

    assert "calendar.list_events" in selected
    assert "task.create" in selected
    assert len([item for item in selected if item.startswith("gmail.")]) <= 2
    assert len([item for item in selected if item.startswith("task.")]) == 1


@pytest.mark.asyncio
async def test_read_analysis_gets_small_pair_instead_of_entire_provider_surface():
    registry = CapabilityRegistry()
    registry.register(_DiscoveryFixtureProvider())
    resolver = FactoryCapabilityIntentResolver(
        registry=registry,
        scope_id="workspace-1",
        authority={"messaging:read"},
    )

    selected = await resolver(("email_analysis",))

    assert 1 <= len(selected) <= 2
    assert all(item.startswith("gmail.") for item in selected)


@pytest.mark.asyncio
async def test_explicit_operation_intent_projects_one_tool_schema():
    registry = CapabilityRegistry()
    registry.register(_DiscoveryFixtureProvider())
    resolver = FactoryCapabilityIntentResolver(
        registry=registry,
        scope_id="workspace-1",
        authority={"tasks:write"},
    )

    selected = await resolver(("task_create",))

    assert len(selected) == 1
    assert selected[0].startswith("task.create")


@pytest.mark.asyncio
async def test_pseudo_intent_normalization_does_not_weaken_operation_gate():
    registry = CapabilityRegistry()
    registry.register(_DiscoveryFixtureProvider())
    resolver = FactoryCapabilityIntentResolver(
        registry=registry,
        scope_id="workspace-1",
        authority={"calendar:read"},
    )

    selected = await resolver(("calendar_create",))

    assert selected == []
