import asyncio

import pytest

from packages.coding_harness.opencode_agent import CodingHarnessError, OpenCodeStyleCodingAgent
from packages.custom_software.source_bundles import SourceFile


class DirectBuildModel:
    """The first model turn writes source directly; no mandatory plan turn exists."""

    def __init__(self):
        self.calls = 0
        self.tool_names = []

    async def chat(self, messages, tools=None):
        self.calls += 1
        self.tool_names.append({item["function"]["name"] for item in (tools or [])})
        if self.calls != 1:
            raise AssertionError("direct build should complete in one model turn")
        return {
            "role": "assistant",
            "content": "Implementing directly against the approved specification.",
            "tool_calls": [
                {"function": {"name": "write", "arguments": {"path": "app.py", "content": "def add(a,b): return a+b\n"}}},
                {"function": {"name": "write", "arguments": {"path": "test_app.py", "content": "import unittest\nfrom app import add\nclass T(unittest.TestCase):\n def test_add(self): self.assertEqual(add(2,3),5)\n"}}},
                {"function": {"name": "finish", "arguments": {"summary": "Implemented."}}},
            ],
        }


class VisualEditModel:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            names = {item["function"]["name"] for item in tools or []}
            assert "inspect_visual" in names
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "inspect_visual", "arguments": {}}}],
            }
        if self.calls == 2:
            tool_messages = [item for item in messages if item.get("role") == "tool"]
            assert any("#hero-title" in item.get("content", "") for item in tool_messages)
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "grep", "arguments": {"query": "Welcome"}}},
                    {"function": {"name": "edit", "arguments": {"path": "index.html", "old": "<h1>Welcome</h1>", "new": "<h1>Welcome home</h1>"}}},
                    {"function": {"name": "finish", "arguments": {"summary": "Updated selected heading."}}},
                ],
            }
        raise AssertionError("unexpected visual edit turn")


class PlanPermissionModel:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        names = {item["function"]["name"] for item in tools or []}
        assert "write" not in names
        assert "edit" not in names
        assert "finish_plan" in names
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "finish_plan", "arguments": {"plan": "Read app.py, then change the requested behavior and its tests."}}}],
        }


class DoomLoopModel:
    async def chat(self, messages, tools=None):
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "list", "arguments": {"prefix": ""}}}],
        }


class MissingTestRecoveryModel:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "write", "arguments": {"path": "app.py", "content": "def winner(): return 'A'\n"}}},
                    {"function": {"name": "finish", "arguments": {"summary": "Implemented."}}},
                ],
            }
        assert any("no executable test file" in str(item.get("content", "")) for item in messages if item.get("role") == "tool")
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "write", "arguments": {"path": "test_app.py", "content": "from app import winner\ndef test_winner(): assert winner() == 'A'\n"}}},
                {"function": {"name": "finish", "arguments": {"summary": "Implemented with tests.", "verification": ["python -m unittest"]}}},
            ],
        }


def test_build_can_start_directly_with_project_tools():
    model = DirectBuildModel()
    result = asyncio.run(OpenCodeStyleCodingAgent(client=model, max_steps=6).build("Approved tiny Python specification"))
    assert model.calls == 1
    assert "write" in model.tool_names[0]
    assert result.changed_paths == ["app.py", "test_app.py"]


def test_rejected_finish_returns_evidence_and_agent_adds_missing_tests():
    model = MissingTestRecoveryModel()
    result = asyncio.run(OpenCodeStyleCodingAgent(client=model, max_steps=6).build("Approved decision capability"))
    assert model.calls == 2
    assert [item.path for item in result.files] == ["app.py", "test_app.py"]
    assert any(item.tool == "finish" and not item.ok for item in result.trace)
    assert any(item.tool == "finish" and item.ok for item in result.trace)


def test_visual_edit_observes_selected_dom_context_before_source_change():
    files = [
        SourceFile("index.html", b"<h1>Welcome</h1>", "seed"),
        SourceFile("test_app.py", b"def test_placeholder(): assert True\n", "seed"),
    ]
    context = {
        "selection": {"selector": "#hero-title", "tag": "h1", "text": "Welcome"},
        "viewport": {"width": 1280, "height": 720},
    }
    result = asyncio.run(
        OpenCodeStyleCodingAgent(client=VisualEditModel(), max_steps=8).edit(
            "Approved website specification",
            files,
            "Make the selected heading friendlier",
            context=context,
        )
    )
    assert result.changed_paths == ["index.html"]
    assert "Welcome home" in next(item.content.decode() for item in result.files if item.path == "index.html")
    assert any(item.tool == "inspect_visual" and item.ok for item in result.trace)


def test_plan_mode_is_read_only_by_tool_permission():
    plan = asyncio.run(
        OpenCodeStyleCodingAgent(client=PlanPermissionModel(), max_steps=4).plan(
            "Approved existing project specification",
            [SourceFile("app.py", b"print('x')\n", "seed")],
        )
    )
    assert "Read app.py" in plan


def test_repeated_identical_tool_calls_trigger_doom_loop_guard():
    with pytest.raises(CodingHarnessError, match="repeated the same list tool call"):
        asyncio.run(OpenCodeStyleCodingAgent(client=DoomLoopModel(), max_steps=8).build("Approved specification"))
