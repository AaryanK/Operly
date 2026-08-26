from types import SimpleNamespace

import pytest

import packages.agents.control_plane.worker_adapter as worker_module
from packages.agents.control_plane import AgentRuntimeWorker, ContextCapsule, StageSpec


class InvokingRuntime:
    def __init__(self, *, max_steps):
        self.max_steps = max_steps

    async def run(self, **kwargs):
        observation = await kwargs["invoke"](
            "messaging.send",
            {"text": "hello"},
            "model-call-7",
        )
        return {
            "message": "waiting",
            "execution_truth": {
                "status": "WAITING_APPROVAL",
                "verified": False,
            },
            "trace": [
                SimpleNamespace(
                    capability_id="messaging.send",
                    observation=observation,
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


def test_factory_causation_round_trip_is_bounded():
    causation = worker_module.factory_action_call_id(
        "11111111-2222-3333-4444-555555555555",
        "send-the-approved-email",
        2,
        "provider-tool-call-with-arbitrary-length",
    )

    assert len(causation) <= 160
    assert causation.startswith("factory:11111111-2222-3333-4444-555555555555:")
    assert (
        worker_module.factory_run_id_from_causation(causation)
        == "11111111-2222-3333-4444-555555555555"
    )
    assert worker_module.factory_run_id_from_causation("ordinary-call") is None


@pytest.mark.asyncio
async def test_worker_decorates_action_call_id_but_keeps_root_runtime_id(monkeypatch):
    monkeypatch.setattr(worker_module, "AgentRuntime", InvokingRuntime)
    seen = []

    async def invoke(name, arguments, call_id):
        seen.append((name, dict(arguments), call_id))
        return {
            "status": "WAITING_APPROVAL",
            "action_id": "action-1",
            "approval_id": "approval-1",
            "observation": {},
        }

    adapter = AgentRuntimeWorker(
        schemas=lambda: [_tool("messaging.send")],
        invoke=invoke,
        model_resolver=lambda _role: object(),
        inference_metadata={
            "runtime_run_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "tenant_id": "tenant-1",
        },
    )
    result = await adapter(
        StageSpec("send", "Send the approved message"),
        ContextCapsule(
            stage_id="send",
            objective="Send the approved message",
            capability_ids=("messaging.send",),
        ),
        1,
        None,
    )

    assert result.status == "waiting_approval"
    assert len(seen) == 1
    assert seen[0][0] == "messaging.send"
    assert (
        worker_module.factory_run_id_from_causation(seen[0][2])
        == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    )
