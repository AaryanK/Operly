from packages.capabilities.defaults import default_registry
from packages.capabilities.session_view import DEFAULT_KERNEL_IDS, SessionCapabilityView


def _tool_ids(view: SessionCapabilityView) -> set[str]:
    return {
        item["function"]["name"]
        for item in view.schemas()
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }


def test_authorized_capabilities_are_not_bulk_exposed():
    registry = default_registry(set())
    view = SessionCapabilityView(
        registry,
        "tenant-test",
        {"crm:read", "crm:write", "orders:write"},
    )

    tools = _tool_ids(view)
    # Kernel membership is still authority-gated. This fake principal omitted
    # model:invoke, so escalation tools must not appear merely because they are kernel IDs.
    assert tools == set(DEFAULT_KERNEL_IDS) - {"model.invoke", "model.deep_reason"}
    assert "crm.list_contacts" not in tools
    assert "crm.create_contact" not in tools
    assert "orders.create" not in tools


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
