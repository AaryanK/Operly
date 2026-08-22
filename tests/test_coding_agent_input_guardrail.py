import asyncio

from packages.coding_harness.opencode_agent import OpenCodeStyleCodingAgent
from packages.custom_software.source_bundles import SourceFile


class InspectTwiceThenActModel:
    def __init__(self):
        self.calls = 0
        self.saw_guardrail = False

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "read", "arguments": {"path": "app.py"}}}
                ],
            }
        if self.calls == 2:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "grep", "arguments": {"query": "old", "prefix": ""}}}
                ],
            }
        if self.calls == 3:
            self.saw_guardrail = any(
                item.get("role") == "user"
                and "two model turns inspecting without changing the workspace" in str(item.get("content") or "")
                for item in messages
            )
            return {
                "role": "assistant",
                "content": "Applying the requested change using the source already inspected.",
                "tool_calls": [
                    {
                        "function": {
                            "name": "edit",
                            "arguments": {
                                "path": "app.py",
                                "old": "return 'old'",
                                "new": "return 'new'",
                            },
                        }
                    },
                    {"function": {"name": "finish", "arguments": {"summary": "Updated label."}}},
                ],
            }
        raise AssertionError("unexpected coding turn")


def _python_files():
    return [
        SourceFile("app.py", b"def label():\n    return 'old'\n", "seed"),
        SourceFile("build.py", b"print('build')\n", "seed"),
        SourceFile(
            "test_app.py",
            b"import unittest\nfrom app import label\nclass T(unittest.TestCase):\n    def test_label(self): self.assertIn(label(), {'old','new'})\n",
            "seed",
        ),
    ]


def test_edit_nudges_after_two_source_inspection_turns_without_progress():
    events = []

    async def progress(event):
        events.append(event)

    model = InspectTwiceThenActModel()
    result = asyncio.run(
        OpenCodeStyleCodingAgent(client=model, max_steps=6, progress_callback=progress).edit(
            "Approved Python application specification",
            _python_files(),
            "Change the label from old to new",
        )
    )

    assert model.calls == 3
    assert model.saw_guardrail is True
    assert result.changed_paths == ["app.py"]
    assert any(item.get("phase") == "guardrail" for item in events)


def test_model_input_progress_event_exposes_safe_packet_metadata():
    events = []

    async def progress(event):
        events.append(event)

    model = InspectTwiceThenActModel()
    asyncio.run(
        OpenCodeStyleCodingAgent(client=model, max_steps=6, progress_callback=progress).edit(
            "Approved Python application specification",
            _python_files(),
            "Change the label from old to new",
        )
    )

    event = next(item for item in events if item.get("phase") == "model_input")
    detail = event["detail"]
    assert detail["mode"] == "edit"
    assert detail["systemPrompt"] == "BUILD_SYSTEM"
    assert detail["specificationChars"] == len("Approved Python application specification")
    assert detail["workspaceFiles"] == ["app.py", "build.py", "test_app.py"]
    assert detail["workspaceFileCount"] == 3
    assert "read" in detail["toolNames"]
    assert "edit" in detail["toolNames"]
    assert detail["initialMessageChars"] > detail["specificationChars"]
    assert "old" not in str(detail)
