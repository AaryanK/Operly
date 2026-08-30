from packages.agent_runtime_v2.contracts import Observation, Plan, RunState, Step, StepState
from packages.business_brain.runtime_v2_resume import (
    _checkpoint_lifecycle,
    _state,
    _verified_mutation_receipts,
)


def _waiting_state(status: str) -> dict:
    return {
        "run_id": "run-1",
        "objective": "Send the update after checking the record",
        "status": "waiting",
        "stop_reason": "waiting_external",
        "mutation_epoch": 1,
        "model_calls": 2,
        "token_usage": {"input_tokens": 10, "output_tokens": 4},
        "runtime_context": {"timezone": "UTC"},
        "plan": {
            "goal": "Send the update after checking the record",
            "constraints": [],
            "final_step_id": "send",
            "blocked": [],
            "steps": [
                {
                    "id": "send",
                    "objective": "Send it",
                    "capabilities": ["messaging.send"],
                    "depends_on": [],
                    "mutating": True,
                    "run_if": None,
                    "requires_complete_coverage": False,
                }
            ],
        },
        "steps": {
            "send": {
                "id": "send",
                "status": "waiting",
                "summary": "messaging.send is waiting.",
                "output": None,
                "observations": [
                    {
                        "capability_id": "messaging.send",
                        "arguments": {"message": "hello"},
                        "result": {
                            "ok": True,
                            "status": status,
                            "action_id": "action-1",
                        },
                        "signature": "sig-1",
                        "memoized": False,
                    }
                ],
                "model_calls": 1,
                "input_tokens": 5,
                "output_tokens": 2,
            }
        },
    }


def test_waiting_approval_is_distinct_from_generic_external_wait():
    assert _checkpoint_lifecycle(_waiting_state("WAITING_APPROVAL")) == "waiting_approval"
    assert _checkpoint_lifecycle(_waiting_state("RUNNING")) == "waiting_external"


def test_runtime_v2_state_round_trips_from_durable_projection():
    restored = _state(_waiting_state("WAITING_APPROVAL"))
    assert restored.run_id == "run-1"
    assert restored.status == "waiting"
    assert restored.plan.final_step_id == "send"
    assert restored.steps["send"].status == "waiting"
    assert restored.steps["send"].observations[0].result["action_id"] == "action-1"
    assert restored.input_tokens == 10
    assert restored.output_tokens == 4


def test_only_verified_mutations_become_resume_replay_receipts():
    plan = Plan(
        goal="Do work",
        constraints=(),
        steps=(
            Step("read", "Read", ("gmail.search",)),
            Step("write", "Write", ("messaging.send",), mutating=True),
        ),
        final_step_id="write",
    )
    state = RunState(
        run_id="run-2",
        objective="Do work",
        plan=plan,
        steps={
            "read": StepState(
                id="read",
                status="completed",
                observations=[
                    Observation(
                        "gmail.search",
                        {"query": "x"},
                        {"ok": True, "status": "VERIFIED"},
                        "read-sig",
                    )
                ],
            ),
            "write": StepState(
                id="write",
                status="pending",
                observations=[
                    Observation(
                        "messaging.send",
                        {"message": "hello"},
                        {"ok": True, "status": "VERIFIED", "action_id": "action-2"},
                        "write-sig",
                    )
                ],
            ),
        },
    )

    receipts = _verified_mutation_receipts(state)
    assert receipts == [
        (
            "messaging.send",
            {"message": "hello"},
            {"ok": True, "status": "VERIFIED", "action_id": "action-2"},
        )
    ]
