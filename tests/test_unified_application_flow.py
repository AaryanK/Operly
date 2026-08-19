from packages.capabilities.agent_harness import PluginInvocationContext
from packages.capabilities.defaults import default_registry


def test_canonical_registry_exposes_migrated_plugins_once():
    registry = default_registry(set())
    ids = [definition.id for definition in registry.definitions()]

    expected = {
        "business.summary",
        "crm.create_contact",
        "crm.create_lead",
        "crm.search_leads",
        "crm.update_lead",
        "crm.update_stage",
        "catalog.create_item",
        "inventory.adjust",
        "orders.create",
        "quotes.create",
        "calendar.create_internal_event",
        "tasks.list",
        "tasks.create",
        "tasks.complete",
        "memory.store",
        "memory.search",
        "messages.search",
        "operations.brief",
        "operations.scan",
        "operations.audit",
        "operations.generate_plan",
        "studio.list_projects",
        "studio.create_project",
        "studio.generate_site",
        "studio.list_versions",
        "studio.publish_version",
        "studio.public_url",
        "reminders.create",
    }
    assert expected.issubset(set(ids))
    assert all(ids.count(capability) == 1 for capability in expected)


def test_legacy_undotted_agent_tools_are_not_in_canonical_registry():
    ids = {definition.id for definition in default_registry(set()).definitions()}
    assert {
        "create_task",
        "list_tasks",
        "complete_task",
        "remember_fact",
        "search_memory",
        "search_messages",
        "create_contact",
        "create_lead",
        "update_lead_stage",
        "create_catalog_item",
        "adjust_inventory",
        "create_order",
        "create_quote",
        "schedule_appointment",
        "create_reminder",
        "request_approval",
    }.isdisjoint(ids)


def test_discord_delivery_metadata_is_runtime_private():
    context = PluginInvocationContext(
        tenant_id="tenant",
        user_id="user",
        role="owner",
        objective="remind me",
        channel="discord",
        metadata={
            "discord_channel_id": "123",
            "discord_user_id": "456",
            "discord_guild_id": "789",
        },
    )
    reminder = next(
        item
        for item in default_registry(set()).definitions()
        if item.id == "reminders.create"
    )
    model_fields = set(reminder.input_schema["properties"])

    assert context.metadata["discord_channel_id"] == "123"
    assert "discord_channel_id" not in model_fields
    assert "discord_user_id" not in model_fields
    assert "discord_guild_id" not in model_fields
