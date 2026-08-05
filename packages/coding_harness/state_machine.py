from .contracts import ExecutionState

TRANSITIONS = {
    ExecutionState.awaiting_approval: {ExecutionState.queued, ExecutionState.cancelled},
    ExecutionState.queued: {ExecutionState.provisioning, ExecutionState.cancelled},
    ExecutionState.provisioning: {ExecutionState.implementing, ExecutionState.blocked, ExecutionState.cancelled},
    ExecutionState.implementing: {ExecutionState.building, ExecutionState.blocked, ExecutionState.cancelled},
    ExecutionState.building: {ExecutionState.testing, ExecutionState.diagnosing, ExecutionState.exhausted, ExecutionState.cancelled},
    ExecutionState.testing: {ExecutionState.running, ExecutionState.diagnosing, ExecutionState.exhausted, ExecutionState.cancelled},
    ExecutionState.running: {ExecutionState.inspecting, ExecutionState.diagnosing, ExecutionState.cancelled},
    ExecutionState.inspecting: {ExecutionState.acceptance_passed, ExecutionState.diagnosing, ExecutionState.cancelled},
    ExecutionState.diagnosing: {ExecutionState.repairing, ExecutionState.blocked, ExecutionState.exhausted, ExecutionState.cancelled},
    ExecutionState.repairing: {ExecutionState.building, ExecutionState.blocked, ExecutionState.exhausted, ExecutionState.cancelled},
}


def transition(current: ExecutionState, target: ExecutionState) -> ExecutionState:
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError(f"Invalid harness transition: {current} -> {target}")
    return target
