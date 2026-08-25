from __future__ import annotations

import pytest

import packages.agents.controller as controller_module
from packages.agents.capability_rescue import (
    attempt_capability_rescue,
    has_execution_evidence,
)
from packages.agents.controller import AgentRunController
from packages.agents.runtime import AgentTraceEntry
from packages.agents.verification import RunGoalVerification


_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "capability.search",
        "description": "Semantically discover an authorized capability.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    },
}
_DESCRIBE_TOOL = {
    "type": "function",
    "function": {
        "name": "capability.describe",
        "description": "Expose exact schemas for discovered capabilities.",
        "parameters": {
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["ids"],
        },
    },
}
_DOCX_TOOL = {
    "type": "function",
    "function": {
        "name": "files.create_document",
        "description": "Create a durable PDF, DOCX, TXT or Markdown document.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "output_format": {"type": "string", "enum": ["docx"]},
                "filename": {"type": "string"},
            },
            "required": ["content", "output_format"],
        },
    },
}


class _DocxRescueModel:
    def __init__(self) -> None:
        self.calls = 0
        self.tool_surfaces: list[set[str]] = []

    async def chat(self, messages, tools):
        self.calls += 1
        names = {
            str((tool.get("function") or {}).get("name") or "")
            for tool in tools
            if isinstance(tool, dict)
        }
        self.tool_surfaces.append(names)
        if self.calls == 1:
            assert "files.create_document" not in names
            return {
                "role": "assistant",
                "content": (
                    "As an AI, I don't have the ability to directly generate or hand over a DOCX file."
                ),
            }
        if self.calls == 2:
            assert "files.create_document" in names
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "docx-create-1",
                        "type": "function",
                        "function": {
                            "name": "files.create_document",
                            "arguments": (
                                '{"content":"Analysis report","output_format":"docx",'
                                '"filename":"analysis-report.docx"}'
                            ),
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "Created the DOCX and attached it."}


class _SatisfiedVerifier:
    """The rescue test owns discovery behavior, not requirements-model availability."""

    async def verify(self, **kwargs):
        del kwargs
        return RunGoalVerification(
            True,
            verified=("A provider-verified DOCX artifact exists.",),
            reason="test_verified_artifact",
        )


@pytest.mark.asyncio
async def test_discord_docx_request_rescues_hidden_file_authoring_capability(monkeypatch):
    async def no_resume(*, objective, metadata):
        return None

    async def checkpoint(**kwargs):
        return None

    monkeypatch.setattr(controller_module, "find_resumable_agent_run", no_resume)
    monkeypatch.setattr(controller_module, "checkpoint_agent_run", checkpoint)

    exposed = False
    invocations: list[str] = []

    async def schemas():
        tools = [_SEARCH_TOOL, _DESCRIBE_TOOL]
        if exposed:
            tools.append(_DOCX_TOOL)
        return tools

    async def invoke(name, arguments, call_id):
        nonlocal exposed
        invocations.append(name)
        if name == "capability.search":
            assert arguments["query"] == "wrap this to a docx and give that to me"
            return {
                "ok": True,
                "status": "VERIFIED",
                "observation": {
                    "capabilities": [
                        {
                            "id": "files.create_document",
                            "authorized": True,
                            "display_name": "Create document",
                            "description": "Create an exact DOCX from known content.",
                            "availability": {"available": True, "reason": "available"},
                            "semantic_score": 0.91,
                            "lexical_score": 3.0,
                        }
                    ]
                },
                "lifecycle": {"completed": True, "verified": True},
            }
        if name == "capability.describe":
            assert arguments == {"ids": ["files.create_document"]}
            exposed = True
            return {
                "ok": True,
                "status": "VERIFIED",
                "observation": {
                    "capabilities": [
                        {
                            "id": "files.create_document",
                            "authorized": True,
                            "schema": _DOCX_TOOL,
                            "availability": {"available": True},
                        }
                    ]
                },
                "lifecycle": {"completed": True, "verified": True},
            }
        assert name == "files.create_document"
        assert arguments["output_format"] == "docx"
        return {
            "ok": True,
            "status": "VERIFIED",
            "observation": {
                "artifact_id": "artifact-docx-1",
                "artifact_ids": ["artifact-docx-1"],
                "artifacts": [
                    {
                        "artifact_id": "artifact-docx-1",
                        "filename": "analysis-report.docx",
                        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    }
                ],
                "output_format": "docx",
            },
            "lifecycle": {"completed": True, "verified": True},
        }

    messages = [
        {"role": "system", "content": "You are Operly."},
        {"role": "user", "content": "wrap this to a docx and give that to me"},
    ]
    model = _DocxRescueModel()
    result = await AgentRunController(
        max_replans=0,
        verifier=_SatisfiedVerifier(),
    ).run(
        objective="wrap this to a docx and give that to me",
        model=model,
        messages=messages,
        schemas=schemas,
        invoke=invoke,
        max_steps=6,
        inference_metadata={
            "channel": "discord",
            "surface": "workspace_shared",
            "tenant_id": "workspace-1",
            "user_id": "user-1",
        },
    )

    assert invocations == [
        "capability.search",
        "capability.describe",
        "files.create_document",
    ]
    assert result["capability_rescues"] == 1
    assert result["rescued_capability_ids"] == ["files.create_document"]
    assert result["execution_truth"]["status"] == "VERIFIED"
    assert result["execution_truth"]["verified"] is True
    assert result["message"] == "Created the DOCX and attached it."
    assert model.calls == 3
    assert "files.create_document" not in model.tool_surfaces[0]
    assert "files.create_document" in model.tool_surfaces[1]
    assert all("I don't have the ability" not in str(message.get("content") or "") for message in messages)


@pytest.mark.asyncio
async def test_rescue_stops_after_semantic_search_when_no_relevant_capability_exists():
    calls: list[str] = []
    messages = [
        {"role": "user", "content": "Explain the difference between two abstract ideas."},
        {"role": "assistant", "content": "Here is the explanation."},
    ]

    async def invoke(name, arguments, call_id):
        calls.append(name)
        assert name == "capability.search"
        return {
            "ok": True,
            "status": "VERIFIED",
            "observation": {
                "capabilities": [
                    {
                        "id": "files.create_document",
                        "authorized": True,
                        "availability": {"available": True},
                        "semantic_score": 0.20,
                        "lexical_score": 0.0,
                    }
                ]
            },
        }

    result = await attempt_capability_rescue(
        objective="Explain the difference between two abstract ideas.",
        messages=messages,
        invoke=invoke,
    )

    assert result.attempted is True
    assert result.applied is False
    assert result.reason == "no_relevant_authorized_capability"
    assert calls == ["capability.search"]
    assert messages[-1]["content"] == "Here is the explanation."


def test_discovery_metadata_is_not_execution_evidence():
    trace = [
        AgentTraceEntry("capability.search", {}, {"status": "VERIFIED"}),
        AgentTraceEntry("capability.describe", {}, {"status": "VERIFIED"}),
    ]
    assert has_execution_evidence(trace) is False
    trace.append(AgentTraceEntry("files.create_document", {}, {"status": "VERIFIED"}))
    assert has_execution_evidence(trace) is True
