import json
from types import SimpleNamespace

import pytest

import packages.agents.control_plane.worker_adapter as worker_module
from packages.agents.control_plane import AgentRuntimeWorker, ContextCapsule, Defect, StageSpec


class RepeatingReadRuntime:
    seen_initial_payloads = []
    seen_continuation_payloads = []

    def __init__(self, *, max_steps, execution_budget=None, inference_budget=None):
        self.max_steps = max_steps
        self.execution_budget = execution_budget
        self.inference_budget = inference_budget

    async def run(self, **kwargs):
        initial = list(kwargs["messages"])
        RepeatingReadRuntime.seen_initial_payloads.append(
            json.loads(initial[1]["content"])
        )

        arguments = {"query": "after:2026-08-22"}
        first = await kwargs["invoke"]("gmail.search", arguments, "call-1")
        messages = [
            *initial,
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "gmail.search",
                            "arguments": arguments,
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "tool_name": "gmail.search",
                "content": json.dumps(first),
            },
        ]
        reduced = kwargs["reduce_working_messages"](messages)
        RepeatingReadRuntime.seen_continuation_payloads.append(
            json.loads(reduced[1]["content"])
        )

        second = await kwargs["invoke"]("gmail.search", arguments, "call-2")
        return {
            "message": "Email scan complete.",
            "execution_truth": {"status": "VERIFIED", "verified": True},
            "trace": [
                SimpleNamespace(capability_id="gmail.search", observation=first),
                SimpleNamespace(capability_id="gmail.search", observation=second),
            ],
            "stop_reason": "completed",
            "stopped": False,
            "budget": {},
        }


class MutationInvalidationRuntime:
    def __init__(self, *, max_steps, execution_budget=None, inference_budget=None):
        self.max_steps = max_steps

    async def run(self, **kwargs):
        arguments = {"query": "after:2026-08-22"}
        first = await kwargs["invoke"]("gmail.search", arguments, "read-1")
        mutation = await kwargs["invoke"](
            "task.create",
            {
                "title": "Follow up",
                "objective": "Follow up on the email",
                "trigger": {"kind": "once", "run_at": "2026-08-30T09:00:00-05:00"},
            },
            "write-1",
        )
        second = await kwargs["invoke"]("gmail.search", arguments, "read-2")
        return {
            "message": "done",
            "execution_truth": {"status": "VERIFIED", "verified": True},
            "trace": [
                SimpleNamespace(capability_id="gmail.search", observation=first),
                SimpleNamespace(capability_id="task.create", observation=mutation),
                SimpleNamespace(capability_id="gmail.search", observation=second),
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


def _verified_result(name, arguments):
    return {
        "ok": True,
        "status": "VERIFIED",
        "plugin": name,
        "verification": {"success": True},
        "observation": {"arguments": arguments, "count": 3},
    }


@pytest.mark.asyncio
async def test_verified_read_survives_turn_reset_and_repair_attempt(monkeypatch):
    monkeypatch.setattr(worker_module, "AgentRuntime", RepeatingReadRuntime)
    RepeatingReadRuntime.seen_initial_payloads = []
    RepeatingReadRuntime.seen_continuation_payloads = []
    connector_calls = []

    async def invoke(name, arguments, _call_id):
        connector_calls.append((name, dict(arguments)))
        return _verified_result(name, arguments)

    adapter = AgentRuntimeWorker(
        schemas=lambda: [_tool("gmail.search")],
        invoke=invoke,
        model_resolver=lambda _role: object(),
        inference_metadata={"runtime_run_id": "run-working-state"},
    )
    stage = StageSpec("scan_emails", "Scan recent email")
    capsule = ContextCapsule(
        stage_id=stage.id,
        objective=stage.objective,
        capability_ids=("gmail.search",),
    )

    first = await adapter(stage, capsule, 1, None)
    defect = Defect(
        stage_id=stage.id,
        validator_id="worker.exit_status",
        expected="successful worker result",
        observed="failed",
        retryable=True,
    )
    second = await adapter(stage, capsule, 2, defect)

    # Four model-requested reads across two disposable attempts produce one connector hit.
    assert connector_calls == [("gmail.search", {"query": "after:2026-08-22"})]
    assert first.external_actions == 1
    assert second.external_actions == 0

    # The observation is projected immediately into the next turn.
    continuation_state = RepeatingReadRuntime.seen_continuation_payloads[0][
        "context_capsule"
    ]["working_state"]
    assert len(continuation_state) == 1
    assert continuation_state[0]["capability_id"] == "gmail.search"
    assert continuation_state[0]["arguments"]["query"] == "after:2026-08-22"

    # A fresh repair attempt starts with the same verified observation instead of only
    # receiving the previous failure fingerprint.
    repair_state = RepeatingReadRuntime.seen_initial_payloads[1]["context_capsule"][
        "working_state"
    ]
    assert len(repair_state) == 1
    assert repair_state[0]["status"] == "verified"
    assert repair_state[0]["cache_hits"] >= 1
    assert first.evidence["working_state"][0]["capability_id"] == "gmail.search"


@pytest.mark.asyncio
async def test_successful_mutation_invalidates_read_memoization(monkeypatch):
    monkeypatch.setattr(worker_module, "AgentRuntime", MutationInvalidationRuntime)
    connector_calls = []

    async def invoke(name, arguments, _call_id):
        connector_calls.append(name)
        return _verified_result(name, arguments)

    adapter = AgentRuntimeWorker(
        schemas=lambda: [_tool("gmail.search"), _tool("task.create")],
        invoke=invoke,
        model_resolver=lambda _role: object(),
        inference_metadata={"runtime_run_id": "run-cache-invalidation"},
    )
    stage = StageSpec("act", "Read, act, then verify")
    capsule = ContextCapsule(
        stage_id=stage.id,
        objective=stage.objective,
        capability_ids=("gmail.search", "task.create"),
    )

    await adapter(stage, capsule, 1, None)

    # The second Gmail read is real because task.create advanced the mutation epoch.
    assert connector_calls == ["gmail.search", "task.create", "gmail.search"]
