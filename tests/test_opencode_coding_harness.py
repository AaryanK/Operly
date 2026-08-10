import asyncio

import pytest

from packages.coding_harness.opencode_agent import OpenCodeStyleCodingAgent, VirtualWorkspace, WorkspacePolicyError


class FakeCodingModel:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return {"role": "assistant", "content": "Create a tiny static calculator with browser logic and a Python unittest that verifies the arithmetic contract."}
        if self.calls == 2:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "write", "arguments": {"path": "index.html", "content": "<!doctype html><title>Calculator</title><input id='a'><input id='b'><button id='add'>Add</button><output id='result'></output><script src='app.js'></script>"}}},
                    {"function": {"name": "write", "arguments": {"path": "app.js", "content": "function add(a,b){return Number(a)+Number(b)}; if(typeof module!=='undefined') module.exports={add};"}}},
                    {"function": {"name": "write", "arguments": {"path": "test_calculator.py", "content": "import unittest\nclass CalculatorContract(unittest.TestCase):\n    def test_addition_contract(self):\n        self.assertEqual(2 + 3, 5)\n"}}},
                    {"function": {"name": "finish", "arguments": {"summary": "Implemented the calculator source tree.", "verification": ["Run the calculator contract test in the isolated runner"]}}},
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
    assert workspace.read("app.py") == "value = 2\n"


def test_opencode_style_agent_authors_real_source_tree_without_execution():
    result = asyncio.run(OpenCodeStyleCodingAgent(client=FakeCodingModel(), max_steps=8).build("Approved calculator specification"))
    paths = [item.path for item in result.files]
    assert paths == ["app.js", "index.html", "test_calculator.py"]
    assert result.summary == "Implemented the calculator source tree."
    assert any(item.tool == "write" and item.path == "index.html" for item in result.trace)
    assert any(item.tool == "finish" for item in result.trace)
