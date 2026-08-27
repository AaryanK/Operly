from types import SimpleNamespace

import pytest

from packages.capabilities.defaults import default_registry
from packages.capabilities.discovery_provider import CapabilityDiscoveryProvider
from packages.capabilities.namespaces import DEFAULT_CAPABILITY_NAMESPACE_TREE
from packages.capabilities.session_view import (
    DEFAULT_KERNEL_IDS,
    DEFAULT_ROOT_OPERATION_IDS,
    SessionCapabilityView,
)
from packages.security.surfaces import SurfaceKind


def _tool_ids(view: SessionCapabilityView, *, stage: str | None = None) -> set[str]:
    return {
        item["function"]["name"]
        for item in view.schemas(stage=stage)
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }


def _context(*, surface: SurfaceKind, authority: set[str]):
    return SimpleNamespace(
        tenant_id="tenant-test",
        invocation={
            "surface": surface.value,
            "authority": sorted(authority),
            "metadata": {"_surface_kind": surface.value},
            "channel": "web",
        },
    )


def test_model_boot_surface_is_navigation_not_business_catalog():
    registry = default_registry(set())
    view = SessionCapabilityView(registry, "tenant-test", {"workspace:read"})

    assert DEFAULT_KERNEL_IDS == {
        "capability.search",
        "capability.expand",
        "capability.describe",
    }
    assert DEFAULT_ROOT_OPERATION_IDS == set()
    assert _tool_ids(view) == set(DEFAULT_KERNEL_IDS)
    assert "software.build" not in _tool_ids(view)
    assert "crm.list_contacts" not in _tool_ids(view)
    assert "event.search" not in _tool_ids(view)
    assert "model.invoke" not in _tool_ids(view)


def test_surface_selects_one_namespace_root():
    tree = DEFAULT_CAPABILITY_NAMESPACE_TREE
    assert tree.root_for(SurfaceKind.PERSONAL_PRIVATE) == "user"
    assert tree.root_for(SurfaceKind.DISCORD_DM) == "user"
    assert tree.root_for(SurfaceKind.WORKSPACE_SHARED) == "workspace"
    assert tree.root_for(SurfaceKind.WORKSPACE_PRIVATE) == "workspace"
    assert tree.root_for(SurfaceKind.DISCORD_GUILD) == "workspace"

    assert tree.allowed("user.workspaces", SurfaceKind.PERSONAL_PRIVATE)
    assert not tree.allowed("workspace.crm", SurfaceKind.PERSONAL_PRIVATE)
    assert tree.allowed("workspace.crm", SurfaceKind.WORKSPACE_SHARED)
    assert not tree.allowed("user.connections", SurfaceKind.WORKSPACE_SHARED)


def test_email_search_resolves_to_scope_specific_gmail_namespace():
    tree = DEFAULT_CAPABILITY_NAMESPACE_TREE
    eligible = {"gmail.search", "gmail.read_message", "gmail.send_email"}

    personal = tree.search(
        "find an email in my inbox",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        eligible_ids=eligible,
    )
    workspace = tree.search(
        "find an email in the inbox",
        surface=SurfaceKind.WORKSPACE_SHARED,
        eligible_ids=eligible,
    )

    assert personal[0]["id"] == "user.connections.google.gmail"
    assert workspace[0]["id"] == "workspace.connections.google.gmail"


def test_studio_search_resolves_to_build_branch_without_worker_primitives():
    tree = DEFAULT_CAPABILITY_NAMESPACE_TREE
    eligible = {
        "software.build",
        "software.edit",
        "software.build.status",
        "software.project.inspect",
        "computer.run_python",
        "artifact.create_text",
        "data.relational",
    }
    rows = tree.search(
        "build a complete software application",
        surface=SurfaceKind.WORKSPACE_SHARED,
        eligible_ids=eligible,
    )
    assert rows[0]["id"] == "workspace.solutions.studio.build"

    expansion = tree.expand(
        "workspace.solutions.studio.build",
        surface=SurfaceKind.WORKSPACE_SHARED,
        eligible_ids=eligible,
    )
    assert expansion["capability_ids"] == [
        "software.build",
        "software.edit",
        "software.build.status",
    ]
    assert "computer.run_python" not in expansion["capability_ids"]
    assert "artifact.create_text" not in expansion["capability_ids"]
    assert "data.relational" not in expansion["capability_ids"]


def test_workspace_cannot_expand_personal_namespace():
    tree = DEFAULT_CAPABILITY_NAMESPACE_TREE
    with pytest.raises(PermissionError):
        tree.expand(
            "user.connections.google.gmail",
            surface=SurfaceKind.WORKSPACE_SHARED,
            eligible_ids={"gmail.search"},
        )


@pytest.mark.asyncio
async def test_discovery_describe_is_namespace_bound():
    registry = default_registry(set())
    discovery = CapabilityDiscoveryProvider(registry)
    registry.register(discovery)
    authority = {"solution:read", "solution:generate", "workspace:read"}
    context = _context(surface=SurfaceKind.WORKSPACE_SHARED, authority=authority)

    expanded = await discovery.execute(
        context,
        "capability.expand",
        {"namespace": "workspace.solutions.studio.build"},
    )
    assert expanded.success
    assert "software.build" in expanded.evidence["capability_ids"]

    described = await discovery.execute(
        context,
        "capability.describe",
        {
            "namespace": "workspace.solutions.studio.build",
            "ids": ["software.build"],
        },
    )
    assert described.success
    assert described.evidence["capabilities"][0]["id"] == "software.build"

    rejected = await discovery.execute(
        context,
        "capability.describe",
        {
            "namespace": "workspace.solutions.studio.build",
            "ids": ["software.project.inspect"],
        },
    )
    assert not rejected.success
    assert rejected.evidence["reason"] == "capability_not_mounted_in_namespace"


def test_describe_observation_exposes_only_exact_described_leaf():
    registry = default_registry(set())
    view = SessionCapabilityView(
        registry,
        "tenant-test",
        {"solution:read", "solution:generate", "workspace:read"},
    )

    view.observe(
        "capability.expand",
        {
            "observation": {
                "namespace": {"id": "workspace.solutions.studio.build"},
                "capability_ids": ["software.build", "software.build.status"],
            }
        },
    )
    assert "software.build" not in _tool_ids(view)

    view.observe(
        "capability.describe",
        {
            "observation": {
                "capabilities": [
                    {"id": "software.build", "authorized": True},
                ]
            }
        },
    )
    assert "software.build" in _tool_ids(view)
    assert "software.build.status" not in _tool_ids(view)
