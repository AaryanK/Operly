from types import SimpleNamespace

import pytest

import packages.agents.control_plane.worker_adapter as worker_module
from packages.agents.control_plane import (
    AgentRuntimeWorker,
    ContextCapsule,
    Defect,
    StageSpec,
)
from packages.agents.control_plane.inference_budget import (
    FactoryInferenceBudget,
    FactoryInferenceBudgetExceeded,
    budgeted_model,
)
from packages.model_runtime import InferenceBudget, InferenceRequest, InferenceResult, ModelUsage


class FakeRuntime:
    seen_messages = []
    seen_tools = []
    seen_metadata = []
    seen_execution_budgets = []
    seen_inference_budgets = []

    def __init__(self, *, max_steps, execution_budget=None, inference_budget=None):
        self.max_steps = max_steps
        self.execution_budget = execution_budget
        self.inference_budget = inference_budget
        FakeRuntime.seen_execution_budgets.append(execution_budget)
        FakeRuntime.seen_inference_budgets.append(inference_budget)

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


class FakeInferenceModel:
    def __init__(self):
        self.last_request = None

    async def infer(self, request):
        self.last_request = request
        return InferenceResult(
            message={"role": "assistant", "content": "done"},
            model_resource_id="fake:model",
            provider="fake",
            provider_model_id="fake-model",
            latency_ms=1,
            usage=ModelUsage(input_tokens=120, output_tokens=30, total_tokens=150),
        )


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
    FakeRuntime.seen_inference_budgets = []


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
    inference_budget = FakeRuntime.seen_inference_budgets[-1]
    assert inference_budget.attempts_per_model == 1
    assert inference_budget.max_models == 2
    assert inference_budget.max_output_tokens == 2_000


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


@pytest.mark.asyncio
async def test_budget_reservations_prevent_parallel_double_spend():
    budget = FactoryInferenceBudget(max_tokens=1_000, max_model_calls=10)
    first = await budget.reserve(700)

    with pytest.raises(FactoryInferenceBudgetExceeded) as raised:
        await budget.reserve(400)

    assert raised.value.reason == "root_token_budget_exhausted"
    await budget.reconcile(first, 300)
    second = await budget.reserve(400)
    await budget.reconcile(second, 200)

    snapshot = budget.snapshot()
    assert snapshot["used_tokens"] == 500
    assert snapshot["model_calls"] == 2


@pytest.mark.asyncio
async def test_budgeted_model_uses_provider_usage_and_caps_output_tokens():
    budget = FactoryInferenceBudget(max_tokens=10_000, max_model_calls=10)
    raw_model = FakeInferenceModel()
    model = budgeted_model(
        raw_model,
        root_budget=budget,
        max_output_tokens=800,
    )
    request = InferenceRequest(
        messages=({"role": "user", "content": "do the task"},),
        budget=InferenceBudget(max_output_tokens=2_000),
    )

    result = await model.infer(request)

    assert result.message["content"] == "done"
    assert raw_model.last_request.budget.max_output_tokens == 800
    assert model.usage == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "model_calls": 1,
    }
    assert budget.snapshot()["used_tokens"] == 150


@pytest.mark.asyncio
async def test_root_model_call_limit_returns_clean_terminal_response():
    budget = FactoryInferenceBudget(max_tokens=10_000, max_model_calls=1)
    model = budgeted_model(
        FakeInferenceModel(),
        root_budget=budget,
        max_output_tokens=500,
    )
    request = InferenceRequest(messages=({"role": "user", "content": "hello"},))

    await model.infer(request)
    stopped = await model.infer(request)

    assert stopped.finish_reason == "budget_exhausted"
    assert model.budget_exhausted.reason == "root_model_call_budget_exhausted"
    assert model.usage["model_calls"] == 1
