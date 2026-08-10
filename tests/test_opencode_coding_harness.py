import asyncio

import pytest

from packages.coding_harness.opencode_agent import OpenCodeStyleCodingAgent, VirtualWorkspace, WorkspacePolicyError
from packages.custom_software.source_bundles import SourceFile


class FakeCodingModel:
    def __init__(self, edit=False):
        self.calls = 0
        self.edit = edit

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return {"role": "assistant", "content": "Use one importable calculator module and runner-executable Node tests."}
        if self.calls == 2 and not self.edit:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "write", "arguments": {"path": "index.html", "content": "<!doctype html><title>Calculator</title><script src='app.js'></script>"}}},
                    {"function": {"name": "write", "arguments": {"path": "app.js", "content": "function add(a,b){return Number(a)+Number(b)}; if(typeof module!=='undefined') module.exports={add};"}}},
                    {"function": {"name": "write", "arguments": {"path": "tests/app.test.js", "content": "const test=require('node:test');const assert=require('node:assert/strict');const {add}=require('../app.js');test('addition',()=>assert.equal(add(2,3),5));"}}},
                    {"function": {"name": "finish", "arguments": {"summary": "Implemented calculator source.", "verification": ["node --test"]}}},
                ],
            }
        if self.calls == 2 and self.edit:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "edit", "arguments": {"path": "index.html", "old": "<title>Calculator</title>", "new": "<title>Green Calculator</title>"}}},
                    {"function": {"name": "finish", "arguments": {"summary": "Updated the requested page title."}}},
                ],
            }
        raise AssertionError("Unexpected model call")


def test_virtual_workspace_blocks_path_escape():
    workspace = VirtualWorkspace()
    with pytest.raises(WorkspacePolicyError):
        workspace.write("../escape.py", "print('no')")


def test_virtual_workspace_exact_edit():
    workspace = VirtualWorkspace()
    workspace.write("app.py", "value = 1\n")
    workspace.edit("app.py", "value = 1", "value = 2")
    assert workspace.raw("app.py") == "value = 2\n"


def test_opencode_style_agent_authors_real_source_tree_without_execution():
    result = asyncio.run(OpenCodeStyleCodingAgent(client=FakeCodingModel(), max_steps=8).build("Approved calculator specification"))
    assert [item.path for item in result.files] == ["app.js", "index.html", "tests/app.test.js"]
    assert result.summary == "Implemented calculator source."
    assert any(item.tool == "write" and item.path == "index.html" for item in result.trace)
    assert any(item.tool == "finish" and item.ok for item in result.trace)


def test_same_agent_edits_existing_source_workspace():
    files = [
        SourceFile("index.html", b"<!doctype html><title>Calculator</title>", "seed"),
        SourceFile("app.js", b"module.exports={add:(a,b)=>a+b};", "seed"),
        SourceFile("tests/app.test.js", b"const test=require('node:test');require('../app.js');", "seed"),
    ]
    result = asyncio.run(OpenCodeStyleCodingAgent(client=FakeCodingModel(edit=True), max_steps=8).edit("Approved calculator specification", files, "Make the page title Green Calculator"))
    assert result.changed_paths == ["index.html"]
    assert "Green Calculator" in next(item.content.decode() for item in result.files if item.path == "index.html")
