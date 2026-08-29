import json

import pytest

from packages.agents.control_plane.contracts import ContextCapsule, StageSpec
from packages.agents.control_plane.stage_prompt_pipeline import FactoryStagePromptPipeline
from packages.agents.control_plane.stage_runtime import FactoryStageRuntime
from packages.agents.runtime import AgentExecutionBudget
from packages.model_runtime import InferenceResult, ModelUsage


def _tool(name):
    return {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {
                "type": "object",
                "properties": {"ref": {"type": "string"}},
            },
        },
    }


class SequencedModel:
    def __init__(self):
        self.requests = []

    async def infer(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            message = {
                "role": "assistant",
                "content": "Use the direct stage capability.",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "files.process",
                            "arguments": json.dumps({"ref": "ctx-1"}),
                        },
                    }
                ],
            }
        else:
            message = {
                "role": "assistant",
                "content": "Stage complete from the returned evidence.",
            }
        return InferenceResult(
            message=message,
            model_resource_id="fake:model",
            provider="fake",
            provider_model_id="fake-model",
            latency_ms=1,
            usage=ModelUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        )


@pytest.mark.asyncio
async def test_factory_stage_runtime_never_replays_materialized_context_after_tool_round():
    marker = "WORKSPACE_HISTORY_MUST_NOT_REPLAY:"
    stage = StageSpec(
        "process",
        "Process the selected file",
        capability_intents=("process file",),
    )
    capsule = ContextCapsule(
        stage_id="process",
        objective=stage.objective,
        context_refs=("ctx-1",),
        capability_ids=("files.process",),
        materialized=(
            {
                "ref": "ctx-1",
                "type": "workspace_message",
                "content": marker + ("x" * 10_000),
            },
        ),
        max_context_chars=18_000,
    )
    prompt = FactoryStagePromptPipeline(stage=stage, capsule=capsule)
    model = SequencedModel()

    async def schemas():
        return [_tool("files.process")]

    async def invoke(name, arguments, call_id):
        assert name == "files.process"
        assert arguments == {"ref": "ctx-1"}
        assert call_id == "call-1"
        return {
            "status": "VERIFIED",
            "artifact_id": "artifact-1",
            "message": "processed " + ("y" * 10_000),
        }

    result = await FactoryStageRuntime(
        max_steps=3,
        execution_budget=AgentExecutionBudget(
            base_steps=3,
            max_steps=3,
            extension_steps=1,
            max_tool_calls=4,
        ),
    ).run(
        model=model,
        messages=prompt.initial_messages(),
        schemas=schemas,
        invoke=invoke,
        reduce_working_messages=prompt.continuation_messages,
        inference_metadata={"runtime_run_id": "factory-replay-regression"},
    )

    assert len(model.requests) == 2
    first_prompt = json.dumps(model.requests[0].messages, ensure_ascii=False, default=str)
    second_prompt = json.dumps(model.requests[1].messages, ensure_ascii=False, default=str)

    assert marker in first_prompt
    assert marker not in second_prompt
    assert "ctx-1" in second_prompt
    assert '"materialized_context_replay": "disabled"' in second_prompt
    assert len(second_prompt) < len(first_prompt)
    assert len(second_prompt) < 6_000
    assert result["execution_truth"]["status"] == "VERIFIED"
    assert len(result["trace"]) == 1
    assert result["budget"]["workingSetResets"] == 1


def test_prompt_pipeline_deduplicates_materialized_refs_and_bounds_initial_payload():
    stage = StageSpec("read", "Read relevant context")
    repeated = {
        "ref": "ctx-1",
        "content": "important " + ("z" * 8_000),
    }
    capsule = ContextCapsule(
        stage_id="read",
        objective=stage.objective,
        context_refs=("ctx-1",),
        materialized=(repeated, dict(repeated)),
        max_context_chars=18_000,
    )

    messages = FactoryStagePromptPipeline(stage=stage, capsule=capsule).initial_messages()
    payload = json.loads(messages[1]["content"])
    materialized = payload["context_capsule"]["materialized"]

    assert len(materialized) == 1
    assert materialized[0]["ref"] == "ctx-1"
    assert "important" in materialized[0]["content_preview"]
    assert len(json.dumps(materialized, ensure_ascii=False)) < 6_500
