import asyncio

from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext
from packages.capabilities.defaults import default_registry
from packages.capabilities.firewall import (
    ActionBackedCapabilityFirewall,
    CapabilityDecision,
    CapabilityInvocation,
)
from packages.capabilities.search_index import CapabilitySearchIndex
from packages.security.execution_context import ExecutionContext
from packages.security.surfaces import (
    SurfaceKind,
    capability_surface_allowed,
    surface_from_legacy_metadata,
)


def test_missing_web_surface_fails_closed_instead_of_becoming_private():
    assert surface_from_legacy_metadata("web", {}) is SurfaceKind.UNKNOWN
    context = PluginInvocationContext(
        tenant_id="workspace-1",
        user_id="user-1",
        role="owner",
        objective="what can you see?",
        channel="web",
        metadata={},
    )
    assert context.surface is SurfaceKind.UNKNOWN
    assert PluginAgentHarness.capability_authorized(
        "account.list_workspaces",
        {"workspace:read"},
        context,
    ) is False
    assert PluginAgentHarness.capability_authorized(
        "context.human.search",
        {"context:human:read"},
        context,
    ) is False


def test_shared_workspace_hides_personal_context_but_keeps_workspace_context():
    context = PluginInvocationContext(
        tenant_id="workspace-1",
        user_id="user-1",
        role="owner",
        objective="find project context",
        channel="web",
        metadata={"shared_surface": True},
    )
    assert context.surface is SurfaceKind.WORKSPACE_SHARED
    assert not capability_surface_allowed("account.list_workspaces", context.surface)
    assert not capability_surface_allowed("context.human.search", context.surface)
    assert not capability_surface_allowed("context.private_workspace_search", context.surface)
    assert capability_surface_allowed("context.tenant.search", context.surface)
    assert capability_surface_allowed("context.conversation.search", context.surface)


def test_explicit_private_direct_surface_can_access_personal_context():
    context = PluginInvocationContext(
        tenant_id="workspace-1",
        user_id="user-1",
        role="owner",
        objective="remember my preference",
        channel="web",
        metadata={"shared_surface": False, "is_direct": True},
    )
    assert context.surface is SurfaceKind.PERSONAL_PRIVATE
    assert capability_surface_allowed("account.list_workspaces", context.surface)
    assert capability_surface_allowed("context.human.search", context.surface)


def test_session_key_separates_shared_and_private_surfaces():
    shared = PluginInvocationContext(
        tenant_id="workspace-1",
        user_id="user-1",
        role="owner",
        objective="x",
        channel="web",
        metadata={"_conversation_id": "conversation-1", "shared_surface": True},
    )
    private = PluginInvocationContext(
        tenant_id="workspace-1",
        user_id="user-1",
        role="owner",
        objective="x",
        channel="web",
        metadata={
            "_conversation_id": "conversation-1",
            "shared_surface": False,
            "is_direct": True,
        },
    )
    assert PluginAgentHarness._session_key(shared) != PluginAgentHarness._session_key(private)


def test_semantic_capability_index_finds_calendar_from_meeting_language():
    registry = default_registry(None)
    definitions = registry.metadata(
        "tenant-test",
        authority={"calendar:read", "calendar:write"},
    )
    hits = CapabilitySearchIndex().search(definitions, "schedule a meeting", limit=5)
    ids = [item.capability_id for item in hits]
    assert any(capability_id.startswith("calendar.") for capability_id in ids)


def test_semantic_search_candidate_set_cannot_add_unauthorized_tools():
    registry = default_registry(None)
    definitions = registry.metadata(
        "tenant-test",
        authority={"calendar:read"},
    )
    hits = CapabilitySearchIndex().search(definitions, "send an email", limit=20)
    candidate_ids = {definition.id for definition in definitions}
    assert all(hit.capability_id in candidate_ids for hit in hits)
    assert "gmail.send_draft" not in candidate_ids


def test_firewall_rechecks_surface_even_if_caller_knows_private_capability_id():
    registry = default_registry(None)
    firewall = ActionBackedCapabilityFirewall(registry)
    request = CapabilityInvocation(
        capability_id="account.list_workspaces",
        arguments={},
        objective="list my workspaces",
    )
    shared = ExecutionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        membership_id="member-1",
        role="owner",
        permissions=frozenset({"workspace:read"}),
        channel="web",
        surface=SurfaceKind.WORKSPACE_SHARED,
    )
    private = ExecutionContext(
        workspace_id="workspace-1",
        user_id="user-1",
        membership_id="member-1",
        role="owner",
        permissions=frozenset({"workspace:read"}),
        channel="web",
        surface=SurfaceKind.PERSONAL_PRIVATE,
    )

    assert asyncio.run(firewall.evaluate(request, shared)) is CapabilityDecision.DENY
    assert asyncio.run(firewall.evaluate(request, private)) is CapabilityDecision.ALLOW
