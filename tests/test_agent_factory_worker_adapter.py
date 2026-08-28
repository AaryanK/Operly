from types import SimpleNamespace

import pytest

import packages.agents.control_plane.worker_adapter as worker_module
from packages.agents.control_plane import (
    AgentRuntimeWorker,
    ContextCapsule,
    Defect,
    StageSpec,
)


class FakeRuntime:
    seen_messages = []
    seen_tools = []
    seen_metadata = []
    seen_execution_budgets = []

    def __init__(self, *, max_steps, execution_budget=None):
        self.max_steps = max_steps
        self.execution_budget = execution_budget
        FakeRuntime.seen_execution_budgets.append(execution_budget)

    async def run(self, **kwargs):
        FakeRuntime.seen_messages.append(list(kwargs["messages"]))
        FakeRuntime.seen_tools.append(await kwargs["schemas"]())
        FakeRuntime.seen_metadata.append(dict(kwargs["inference_metadata"]))
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


class DeferredRuntime(FakeRuntime):
    async def run(self, **kwargs):
        FakeRuntime.seen_messages.append(list(kwargs["messages"]))
        FakeRuntime.seen_tools.append(await kwargs["schemas"]())
        FakeRuntime.seen_metadata.append(dict(kwargs["inference_metadata"]))
        return {
            "message": "Build accepted.",
            "execution_truth": {"status": "VERIFIED", "verified": True},
            "trace": [
                SimpleNamespace(
                    capability_id="software.build",
                    observation={
                        "status": "VERIFIED",
                        "observation": {
                            "deferred": True,
                            "continuation_kind": "software_build",
                            "job_id": "job-1",
                            "project_id": "project-1",
                        },
                    },
                )
            ],
            "stop_reason": "completed",
            "stopped": False,
            "budget": {},
        }


class RejectedRuntime(FakeRuntime):
    async def run(self, **kwargs):
        FakeRuntime.seen_messages.append(list(kwargs["messages"]))
        FakeRuntime.seen_tools.append(await kwargs["schemas"]())
        FakeRuntime.seen_metadata.append(dict(kwargs["inference_metadata"]))
        return {
            "message": "I could not run the action.",
            # AgentRuntime may not classify every durable action status itself. The
            # worker adapter must preserve the terminal status from the observation.
            "execution_truth": None,
            "trace": [
                SimpleNamespace(
                    capability_id="messaging.send",
                    observation={
                        "status": "REJECTED",
                        "action_id": "action-1",
                        "approval_id": "approval-1",
                        "observation": {},
                    },
                )
            ],
            "stop_reason": "completed",
            "stopped": False,
            "budget": {},
        }


def _tool(name):
    return {
        "type": "function",
        "function": {"name": name, "parameters": {"type": "object"}},
    }


def _reset_runtime_observations():
    FakeRuntime.seen_messages = []
    FakeRuntime.seen_tools = []
    FakeRuntime.seen_metadata = []
    FakeRuntime.seen_execution_budgets = []


@pytest.mark.asyncio
async def test_worker_adapter_starts_fresh_stage_prompt_and_filters_tools(monkeypatch):
    monkeypatch.setattr(worker_module, "AgentRuntime", FakeRuntime)
    _reset_runtime_observations()

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
        inference_metadata={"runtime_run_id": "factory-run-1", "tenant_id": "tenant-1"},
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
    tool_ids = {
        item["function"]["name"] for item in FakeRuntime.seen_tools[-1]
    }
    assert tool_ids == {"capability.search", "files.process"}
    messages = FakeRuntime.seen_messages[-1]
    assert len(messages) == 2
    assert "disposable OPERLY factory worker" in messages[0]["content"]
    assert "ctx-1" in messages[1]["content"]
    assert FakeRuntime.seen_metadata[-1]["runtime_run_id"] == "factory-run-1"
    assert FakeRuntime.seen_metadata[-1]["factory_stage_id"] == "pdf"
    assert FakeRuntime.seen_metadata[-1]["factory_attempt"] == 1
    execution_budget = FakeRuntime.seen_execution_budgets[-1]
    assert execution_budget.base_steps == 8
    assert execution_budget.max_steps == 10
    assert execution_budget.max_tool_calls == 24


@pytest.mark.asyncio
async def test_repair_worker_gets_only_structured_defect_not_prior_worker_transcript(monkeypatch):
    monkeypatch.setattr(worker_module, "AgentRuntime", FakeRuntime)
    _reset_runtime_observations()

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


@pytest.mark.asyncio
async def test_deferred_verified_capability_becomes_waiting_external(monkeypatch):
    monkeypatch.setattr(worker_module, "AgentRuntime", DeferredRuntime)
    _reset_runtime_observations()

    adapter = AgentRuntimeWorker(
        schemas=lambda: [_tool("software.build")],
        invoke=lambda *_args: {},
        model_resolver=lambda _role: object(),
        inference_metadata={"runtime_run_id": "factory-run-2"},
    )
    result = await adapter(
        StageSpec("build", "Build software"),
        ContextCapsule(
            stage_id="build",
            objective="Build software",
            capability_ids=("software.build",),
        ),
        1,
        None,
    )

    assert result.status == "waiting_external"
    assert result.evidence["deferred"] is True
    assert result.evidence["job_id"] == "job-1"
    assert result.evidence["project_id"] == "project-1"
    assert FakeRuntime.seen_metadata[-1]["runtime_run_id"] == "factory-run-2"


@pytest.mark.asyncio
async def test_terminal_capability_observation_cannot_become_completed_worker_result(monkeypatch):
    monkeypatch.setattr(worker_module, "AgentRuntime", RejectedRuntime)
    _reset_runtime_observations()

    adapter = AgentRuntimeWorker(
        schemas=lambda: [_tool("messaging.send")],
        invoke=lambda *_args: {},
        model_resolver=lambda _role: object(),
        inference_metadata={"runtime_run_id": "factory-run-3"},
    )
    result = await adapter(
        StageSpec("send", "Send message"),
        ContextCapsule(
            stage_id="send",
            objective="Send message",
            capability_ids=("messaging.send",),
        ),
        1,
        None,
    )

    assert result.status == "rejected"
    assert result.evidence["terminal"] is True
    assert result.evidence["status"] == "REJECTED"
    assert result.evidence["action_id"] == "action-1"