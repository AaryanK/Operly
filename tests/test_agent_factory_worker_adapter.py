from types import SimpleNamespace

import pytest

import packages.agents.control_plane.worker_adapter as worker_module
from packages.agents.control_plane import AgentRuntimeWorker, ContextCapsule, Defect, StageSpec


class FakeRuntime:
    seen_messages = []
    seen_tools = []

    def __init__(self, *, max_steps):
        self.max_steps = max_steps

    async def run(self, **kwargs):
        FakeRuntime.seen_messages.append(list(kwargs["messages"]))
        FakeRuntime.seen_tools.append(await kwargs["schemas"]())
        return {
            "message": "stage output",
            "execution_truth": {"status": "VERIFIED", "verified": True},
            "trace": [
                SimpleNamespace(
                    capability_id="files.process",
                    observation={
                        "status": "VERIFIED",
                        "observation": {
                            "artifact_id": "artifact-1",
                            "evidence_ref": "evidence-1",
                            "processed_count": 400,
                        },
                    },
                )
            ],
            "stop_reason": "completed",
            "stopped": False,
            "budget": {},
        }


def _tool(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


@pytest.mark.asyncio
async def test_worker_adapter_starts_fresh_stage_prompt_and_filters_tools(monkeypatch):
    monkeypatch.setattr(worker_module, "AgentRuntime", FakeRuntime)
    FakeRuntime.seen_messages = []
    FakeRuntime.seen_tools = []

    async def schemas():
        return [
            _tool("capability.search"),
            _tool("files.process"),
            _tool("gmail.send_email"),
        ]

    adapter = AgentRuntimeWorker(
        schemas=schemas,
        invoke=lambda *_args: {},
        model_resolver=lambda _role: object(),
    )
    stage = StageSpec("pdf", "Build the PDF")
    capsule = ContextCapsule(
        stage_id="pdf",
        objective="Build the PDF",
        context_refs=("ctx-1",),
        artifact_refs=("img-1", "img-2"),
        capability_ids=("files.process",),
    )
    result = await adapter(stage, capsule, 1, None)

    assert result.artifacts == ("artifact-1",)
    assert result.evidence_refs == ("evidence-1",)
    tool_ids = {item["function"]["name"] for item in FakeRuntime.seen_tools[-1]}
    assert tool_ids == {"capability.search", "files.process"}
    messages = FakeRuntime.seen_messages[-1]
    assert len(messages) == 2
    assert "disposable OPERLY factory worker" in messages[0]["content"]
    assert "ctx-1" in messages[1]["content"]


@pytest.mark.asyncio
async def test_repair_worker_gets_only_structured_defect_not_prior_worker_transcript(monkeypatch):
    monkeypatch.setattr(worker_module, "AgentRuntime", FakeRuntime)
    FakeRuntime.seen_messages = []
    FakeRuntime.seen_tools = []

    adapter = AgentRuntimeWorker(
        schemas=lambda: [_tool("capability.search")],
        invoke=lambda *_args: {},
        model_resolver=lambda _role: object(),
    )
    defect = Defect(
        stage_id="pdf",
        validator_id="pages",
        expected=400,
        observed=397,
        strategy="pillow",
    )
    await adapter(
        StageSpec("pdf", "Repair PDF"),
        ContextCapsule(stage_id="pdf", objective="Repair PDF"),
        2,
        defect,
    )

    messages = FakeRuntime.seen_messages[-1]
    assert len(messages) == 2
    assert "397" in messages[1]["content"]
    assert "pillow" in messages[1]["content"]
    assert "repair_defect" in messages[1]["content"]
