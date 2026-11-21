"""Personal MCP discovery must not make an ordinary greeting unavailable."""
import asyncio
from types import SimpleNamespace

from packages.mcp.first_party import MAX_TOOLS, discover_scoped_capabilities


class Catalog:
    def __init__(self, queried):
        self.queried = queried
        self.calls = []

    async def available_capabilities(self, db, *, context, query, limit):
        self.calls.append((query, limit))
        return self.queried(query)


def test_unmatched_greeting_uses_same_scope_bounded_catalog():
    tools = [SimpleNamespace(id="tasks.list", scopes=["personal"])]
    catalog = Catalog(lambda q: [] if q else tools)
    result = asyncio.run(discover_scoped_capabilities(
        catalog, object(), context=object(), query="hy"))
    assert result == tools
    assert catalog.calls == [("hy", MAX_TOOLS), (None, MAX_TOOLS)]


def test_matching_request_does_not_expand_discovery():
    tools = [SimpleNamespace(id="tasks.list")]
    catalog = Catalog(lambda q: tools)
    result = asyncio.run(discover_scoped_capabilities(
        catalog, object(), context=object(), query="list my tasks"))
    assert result == tools
    assert catalog.calls == [("list my tasks", MAX_TOOLS)]


def test_no_authorized_tools_remain_unavailable():
    catalog = Catalog(lambda q: [])
    result = asyncio.run(discover_scoped_capabilities(
        catalog, object(), context=object(), query="hello"))
    assert result == []
    assert catalog.calls == [("hello", MAX_TOOLS), (None, MAX_TOOLS)]
