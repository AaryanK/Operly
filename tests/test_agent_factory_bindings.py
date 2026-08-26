import pytest

from packages.agents.control_plane import FactoryCapabilityIntentResolver


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
