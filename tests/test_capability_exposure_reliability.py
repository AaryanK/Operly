from packages.capabilities.defaults import default_registry
from packages.capabilities.session_view import DEFAULT_KERNEL_IDS, SessionCapabilityView


def _tool_ids(view: SessionCapabilityView) -> set[str]:
    return {
        item["function"]["name"]
        for item in view.schemas()
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }


def _tool_description(view: SessionCapabilityView, capability_id: str) -> str:
    for item in view.schemas():
        if not isinstance(item, dict) or not isinstance(item.get("function"), dict):
            continue
        if item["function"].get("name") == capability_id:
            return str(item["function"].get("description") or "")
    return ""


def test_authorized_capabilities_are_not_bulk_exposed():
    registry = default_registry(set())
    view = SessionCapabilityView(
        registry,
        "tenant-test",
        {"workspace:read", "crm:read", "crm:write", "orders:write"},
    )

    tools = _tool_ids(view)
    # Kernel membership is still authority-gated. This fake principal omitted
    # model:invoke, so escalation tools must not appear merely because they are kernel IDs.
    assert tools == set(DEFAULT_KERNEL_IDS) - {"model.invoke", "model.deep_reason"}
    assert "crm.list_contacts" not in tools
    assert "crm.create_contact" not in tools
    assert "orders.create" not in tools


def test_event_discovery_is_permanent_workspace_read_kernel():
    registry = default_registry(set())
    view = SessionCapabilityView(registry, "tenant-test", {"workspace:read"})

    tools = _tool_ids(view)
    assert "event.search" in tools
    assert "event.describe" in tools


def test_model_escalation_kernel_is_visible_when_model_authority_exists():
    registry = default_registry(set())
    view = SessionCapabilityView(
        registry,
        "tenant-test",
        {"model:invoke"},
    )
    tools = _tool_ids(view)
    assert "model.invoke" in tools
    assert "model.deep_reason" in tools


def test_describe_observation_progressively_exposes_exact_schema():
    registry = default_registry(set())
    view = SessionCapabilityView(registry, "tenant-test", {"crm:read"})

    view.observe(
        "capability.describe",
        {
            "observation": {
                "capabilities": [
                    {"id": "crm.list_contacts", "authorized": True},
                ]
            }
        },
    )

    tools = _tool_ids(view)
    assert "crm.list_contacts" in tools
    assert "crm.search_contacts" not in tools


def test_sufficient_search_guides_model_to_describe_before_searching_again():
    registry = default_registry(set())
    view = SessionCapabilityView(registry, "tenant-test", {"workspace:read"})

    view.observe(
        "capability.search",
        {
            "observation": {
                "sufficient_match": True,
                "search_again_recommended": False,
                "ranked_ids": ["task.create", "event.search"],
            }
        },
    )

    search_description = _tool_description(view, "capability.search")
    describe_description = _tool_description(view, "capability.describe")
    assert "task.create" in search_description
    assert "Describe/use those candidates before searching again" in search_description
    assert "task.create" in describe_description

    view.observe(
        "capability.describe",
        {"observation": {"capabilities": [{"id": "event.search", "authorized": True}]}},
    )
    assert "Recent search already found sufficient candidates" not in _tool_description(
        view, "capability.search"
    )


def test_describe_cannot_expose_surface_hidden_capability():
    registry = default_registry(set())
    view = SessionCapabilityView(
        registry,
        "tenant-test",
        {"workspace:read"},
        visible_predicate=lambda capability_id: not capability_id.startswith("account."),
    )

    view.observe(
        "capability.describe",
        {
            "observation": {
                "capabilities": [
                    {"id": "account.list_workspaces", "authorized": True},
                ]
            }
        },
    )

    assert "account.list_workspaces" not in _tool_ids(view)


def test_revoked_authority_removes_previously_exposed_schema():
    registry = default_registry(set())
    view = SessionCapabilityView(registry, "tenant-test", {"crm:read"})
    view.expose(["crm.list_contacts"])
    assert "crm.list_contacts" in _tool_ids(view)

    view.authority = set()
    assert "crm.list_contacts" not in _tool_ids(view)
