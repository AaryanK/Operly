import pytest

from packages.agents.control_plane import (
    FactoryCapabilityIntentResolver,
    StageContextInjector,
    StageSpec,
)
from packages.business_brain.factory_runtime import workspace_factory_enabled


class Availability:
    def __init__(self, available):
        self.available = available


class FakeRegistry:
    def search(self, scope_id, query, *, authority, limit):
        assert scope_id == "workspace-1"
        assert authority == {"files:read"}
        assert limit >= 6
        if "files" in query:
            return [
                {"id": "files.read"},
                {"id": "files.delete"},
                {"id": "gmail.send"},
            ]
        return []

    def availability(self, scope_id, capability_id, *, authority):
        assert scope_id == "workspace-1"
        assert authority == {"files:read"}
        return Availability(capability_id == "files.read")


class FakeView:
    def __init__(self):
        self.exposed = []

    def expose(self, ids):
        self.exposed.extend(ids)


@pytest.mark.asyncio
async def test_capability_intent_resolver_only_exposes_available_authorized_surface():
    view = FakeView()
    resolver = FactoryCapabilityIntentResolver(
        registry=FakeRegistry(),
        scope_id="workspace-1",
        authority={"files:read"},
        visible_predicate=lambda capability_id: not capability_id.startswith("gmail."),
        session_view=view,
    )

    selected = await resolver(["read files from workspace"])

    assert selected == ["files.read"]
    assert view.exposed == ["files.read"]


@pytest.mark.asyncio
async def test_inherited_context_ref_is_reauthorized_before_materialization():
    materialized = []

    async def get(refs):
        materialized.extend(refs)
        # Simulates the authorized binding returning only locators it accepts.
        return [
            {"ref": ref, "content": f"authorized:{ref}"}
            for ref in refs
            if ref != "ctx:denied"
        ]

    capsule = await StageContextInjector(materialize=get).build(
        StageSpec("one", "Use the exact retained referent"),
        inherited_context_refs=("ctx:allowed", "ctx:denied"),
    )

    assert materialized == ["ctx:allowed", "ctx:denied"]
    assert [item["ref"] for item in capsule.materialized] == ["ctx:allowed"]
    # Both remain locators, but a denied locator never becomes payload.
    assert capsule.context_refs == ("ctx:allowed", "ctx:denied")


def test_workspace_factory_switch_is_environment_only(monkeypatch):
    monkeypatch.delenv("OPERLY_WORKSPACE_AGENT_FACTORY", raising=False)
    assert workspace_factory_enabled() is False

    monkeypatch.setenv("OPERLY_WORKSPACE_AGENT_FACTORY", "true")
    assert workspace_factory_enabled() is True

    monkeypatch.setenv("OPERLY_WORKSPACE_AGENT_FACTORY", "0")
    assert workspace_factory_enabled() is False
