from packages.capabilities.defaults import default_registry
from packages.capabilities.session_view import SessionCapabilityView


def _tool_ids(view: SessionCapabilityView) -> set[str]:
    return {
        item["function"]["name"]
        for item in view.schemas()
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }


def test_crm_reads_are_available_without_search_describe_roundtrip():
    registry = default_registry(set())
    view = SessionCapabilityView(
        registry,
        "tenant-test",
        {"crm:read"},
    )

    tools = _tool_ids(view)
    assert "crm.list_contacts" in tools
    assert "crm.search_contacts" in tools
    assert "crm.get_contact" in tools
    assert "crm.search_leads" in tools

    # Consequential CRM writes remain progressive/firewall-controlled rather than
    # being injected just because a related read capability exists.
    assert "crm.create_contact" not in tools
    assert "crm.create_lead" not in tools


def test_connected_google_capabilities_are_immediately_visible_when_authorized():
    enabled = {
        "calendar.list_events",
        "calendar.create_event",
        "calendar.update_event",
        "calendar.delete_event",
    }
    registry = default_registry(enabled)
    view = SessionCapabilityView(
        registry,
        "tenant-test",
        {"calendar:read", "calendar:write"},
    )

    tools = _tool_ids(view)
    assert "calendar.list_events" in tools
    assert "calendar.create_event" in tools
    assert "calendar.update_event" in tools
    assert "calendar.delete_event" in tools


def test_discord_current_context_tools_are_seamless_on_discord_but_hideable_elsewhere():
    registry = default_registry(set())
    authority = {"discord:read", "discord:write"}

    discord_view = SessionCapabilityView(
        registry,
        "tenant-test",
        authority,
        visible_predicate=lambda capability_id: True,
    )
    discord_tools = _tool_ids(discord_view)
    assert "discord.context" in discord_tools
    assert "discord.read_recent_messages" in discord_tools
    assert "discord.send_message" in discord_tools
    assert "discord.add_reaction" in discord_tools

    current_context = {
        "discord.context",
        "discord.read_recent_messages",
        "discord.send_message",
        "discord.add_reaction",
        "discord.create_thread",
    }
    web_view = SessionCapabilityView(
        registry,
        "tenant-test",
        authority,
        visible_predicate=lambda capability_id: capability_id not in current_context,
    )
    web_tools = _tool_ids(web_view)
    assert "discord.context" not in web_tools
    assert "discord.read_recent_messages" not in web_tools
    assert "discord.send_message" not in web_tools
    # A linked-user DM capability is not tied to the current Discord channel and
    # may remain available on an authorized private Operly surface.
    assert "discord.send_dm" in web_tools


def test_revoked_authority_removes_previously_exposed_schema():
    registry = default_registry(set())
    view = SessionCapabilityView(registry, "tenant-test", {"crm:read"})
    assert "crm.list_contacts" in _tool_ids(view)

    view.authority = set()
    assert "crm.list_contacts" not in _tool_ids(view)
