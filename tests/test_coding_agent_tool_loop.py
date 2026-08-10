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
                {"function": {"name": "write", "arguments": {"path": "build.py", "content": "print('build')\n"}}},
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


class SlowCodingModel:
    async def chat(self, messages, tools=None):
        await asyncio.sleep(0.2)
        return {"role": "assistant", "content": "", "tool_calls": []}


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
                {"function": {"name": "write", "arguments": {"path": "build.py", "content": "print('build')\n"}}},
                {"function": {"name": "finish", "arguments": {"summary": "Implemented with tests.", "verification": ["python -m unittest"]}}},
            ],
        }


class RuntimeContractRecoveryModel:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "write", "arguments": {"path": "index.html", "content": "<!doctype html><script>window.ready=true</script>"}}},
                    {"function": {"name": "write", "arguments": {"path": "index.test.js", "content": "const test=require('node:test');test('placeholder',()=>{});"}}},
                    {"function": {"name": "finish", "arguments": {"summary": "Done"}}},
                ],
            }
        assert any("No isolated runner profile matches" in str(item.get("content", "")) for item in messages if item.get("role") == "tool")
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "write", "arguments": {"path": "app.js", "content": "module.exports={ready:true};"}}},
                {"function": {"name": "write", "arguments": {"path": "index.test.js", "content": "const test=require('node:test');const assert=require('node:assert/strict');const app=require('./app.js');test('ready',()=>assert.equal(app.ready,true));"}}},
                {"function": {"name": "finish", "arguments": {"summary": "Runtime-valid source"}}},
            ],
        }


def test_build_can_start_directly_with_project_tools():
    model = DirectBuildModel()
    result = asyncio.run(OpenCodeStyleCodingAgent(client=model, max_steps=6).build("Approved tiny Python specification"))
    assert model.calls == 1
    assert "write" in model.tool_names[0]
    assert result.changed_paths == ["app.py", "build.py", "test_app.py"]


def test_rejected_finish_returns_evidence_and_agent_adds_missing_tests():
    model = MissingTestRecoveryModel()
    result = asyncio.run(OpenCodeStyleCodingAgent(client=model, max_steps=6).build("Approved decision capability"))
    assert model.calls == 2
    assert [item.path for item in result.files] == ["app.py", "build.py", "test_app.py"]
    assert any(item.tool == "finish" and not item.ok for item in result.trace)
    assert any(item.tool == "finish" and item.ok for item in result.trace)


def test_finish_enforces_full_runtime_contract_inside_same_coding_session():
    model = RuntimeContractRecoveryModel()
    result = asyncio.run(OpenCodeStyleCodingAgent(client=model, max_steps=6).build("Approved browser application"))

    assert model.calls == 2
    assert [item.path for item in result.files] == ["app.js", "index.html", "index.test.js"]
    assert [item.ok for item in result.trace if item.tool == "finish"] == [False, True]


def test_progress_callback_reports_model_and_real_tool_activity():
    events = []

    async def progress(event):
        events.append(event)

    asyncio.run(OpenCodeStyleCodingAgent(client=DirectBuildModel(), max_steps=6, progress_callback=progress).build("Approved tiny Python specification"))

    assert events[0]["phase"] == "model"
    assert any(item.get("tool") == "write" and "Creating or replacing" in item["summary"] for item in events)
    assert any(item.get("tool") == "finish" and "finalizing" in item["summary"] for item in events)


def test_visual_edit_observes_selected_dom_context_before_source_change():
    files = [
        SourceFile("index.html", b"<h1>Welcome</h1><script src='app.js'></script>", "seed"),
        SourceFile("app.js", b"module.exports={title:'Welcome'};", "seed"),
        SourceFile("tests/app.test.js", b"const test=require('node:test');require('../app.js');test('loads',()=>{});", "seed"),
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


def test_wall_clock_budget_stops_slow_model_turn_instead_of_running_indefinitely():
    agent = OpenCodeStyleCodingAgent(client=SlowCodingModel(), max_steps=8)
    agent.max_seconds = 1
    agent.model_slice_seconds = 0.01

    with pytest.raises(CodingHarnessError, match="bounded generation window"):
        asyncio.run(agent.build("Approved specification"))
