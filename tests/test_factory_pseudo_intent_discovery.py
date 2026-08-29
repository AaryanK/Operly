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
            "task.create",
            "task_create",
            "Create a task with a title and optional deadline.",
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

    assert selected == ["calendar.list_events", "gmail.search", "task.create"]


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
