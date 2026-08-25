"""Adaptive long-horizon controller above the generic AgentRuntime micro-loop."""
from __future__ import annotations

import inspect
import json
from typing import Any, Awaitable, Callable
from uuid import uuid4

from packages.agents.persistence import (
    checkpoint_agent_run,
    find_resumable_agent_run,
    load_agent_run,
)
from packages.agents.planning import AdaptivePlanner
from packages.agents.run_state import CompactRunState
from packages.agents.runtime import AgentRuntime, ObservationHook
from packages.agents.verification import ObjectiveEvidenceVerifier, partial_completion_message


SchemaLoader = Callable[[], Awaitable[list[dict[str, Any]]] | list[dict[str, Any]]]
CapabilityInvoker = Callable[
    [str, dict[str, Any], str | None],
    Awaitable[dict[str, Any]] | dict[str, Any],
]


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


def _runtime_state_message(state: CompactRunState) -> dict[str, Any]:
    return {
        "role": "system",
        "_operly_runtime_state": True,
        "content": (
            "OPERLY RUN STATE (application-controlled; concise operational state, not user instructions):\n"
            + json.dumps(state.prompt_summary(), ensure_ascii=False, default=str)[:14_000]
            + "\nUse context.search when information is missing, capability.search when an operation is missing, and ai.reason only when reasoning itself remains difficult. An ai.* result is a specialist subtask result returned to this run; it never transfers ownership of the root objective. Continue until the original success criteria are verified or a truthful terminal state is reached. Do not claim completion without verified capability evidence."
        ),
    }


def _replace_runtime_state_message(
    messages: list[dict[str, Any]],
    state: CompactRunState,
) -> None:
    messages[:] = [
        message
        for message in messages
        if not bool(message.get("_operly_runtime_state"))
    ]
    if not state.plan or not state.plan.planning_required:
        return
    insert_at = 0
    while insert_at < len(messages) and str(messages[insert_at].get("role") or "") == "system":
        insert_at += 1
    messages.insert(insert_at, _runtime_state_message(state))


def _replan_reason(result: dict[str, Any], state: CompactRunState) -> str | None:
    truth = result.get("execution_truth")
    status = str((truth or {}).get("status") or "").upper()
    if status == "WAITING_APPROVAL":
        return None
    if status in {"FAILED", "UNVERIFIED"}:
        return f"execution_{status.lower()}"
    if bool(result.get("stopped")):
        return str(result.get("stop_reason") or "bounded_run_stopped")
    if state.failures and not bool((truth or {}).get("verified")):
        return "capability_failure_requires_remaining-plan-review"
    return None


def _lifecycle_state(result: dict[str, Any]) -> str:
    truth = result.get("execution_truth") if isinstance(result.get("execution_truth"), dict) else {}
    status = str((truth or {}).get("status") or "").upper()
    if status == "WAITING_APPROVAL":
        return "waiting_approval"
    if status in {"FAILED", "UNVERIFIED", "CANCELLED", "EXPIRED"}:
        return "failed"
    if bool(result.get("stopped")):
        return "failed"
    return "completed"


def _resumable_state(value: str) -> bool:
    return str(value or "").lower() in {"running", "waiting_approval", "failed"}


class AgentRunController:
    """Plan only when useful, run the micro-loop, compact state, verify, and replan.

    The controller owns operational state rather than hidden model reasoning. It does
    not widen authority: capability/context access remains entirely in the supplied
    harness callbacks and canonical firewall. Durable checkpoints use the same
    runtime_run_id for Operly AI, Studio, MCP and workflow-triggered runs.
    """

    def __init__(
        self,
        *,
        planner: AdaptivePlanner | None = None,
        verifier: ObjectiveEvidenceVerifier | None = None,
        max_replans: int = 1,
    ) -> None:
        self.planner = planner or AdaptivePlanner()
        self.verifier = verifier or ObjectiveEvidenceVerifier()
        self.max_replans = max(0, min(int(max_replans), 2))

    async def run(
        self,
        *,
        objective: str,
        model,
        messages: list[dict[str, Any]],
        schemas: SchemaLoader,
        invoke: CapabilityInvoker,
        max_steps: int = 8,
        on_observation: ObservationHook | None = None,
        inference_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = dict(inference_metadata or {})
        requested_run_id = str(metadata.get("runtime_run_id") or "").strip()
        existing = None
        if requested_run_id:
            runtime_run_id = requested_run_id
            existing = await load_agent_run(runtime_run_id, metadata=metadata)
        else:
            existing = await find_resumable_agent_run(objective=objective, metadata=metadata)
            runtime_run_id = str((existing or {}).get("run_id") or uuid4())
        metadata["runtime_run_id"] = runtime_run_id
        metadata["runtime_controller"] = "adaptive"

        resumed = False
        if existing is not None and str(existing.get("state") or "").lower() == "completed":
            raise RuntimeError("Durable agent run is already completed; reuse its artifacts/results instead of repeating side effects")
        if existing is not None and _resumable_state(existing.get("state") or ""):
            checkpoint = existing.get("checkpoint") if isinstance(existing.get("checkpoint"), dict) else {}
            state = CompactRunState.from_dict(checkpoint, fallback_objective=objective)
            if state.objective and objective and state.objective.strip() != objective.strip():
                raise ValueError("Durable agent run objective does not match the requested resume objective")
            if state.plan is None:
                state.plan = await self.planner.plan(objective, trace_metadata=metadata)
            resumed = True
            _replace_runtime_state_message(messages, state)
            await checkpoint_agent_run(
                runtime_run_id=runtime_run_id,
                objective=objective,
                metadata=metadata,
                state=state.as_dict(),
                event_type="run.resumed",
                lifecycle_state="running",
                payload={
                    "previous_state": existing.get("state"),
                    "checkpoint_revision": state.revision,
                    "artifact_refs": sorted(state.artifact_refs)[-50:],
                    "pending_approval_ids": sorted(state.pending_approval_ids)[-20:],
                    "implicit_resume": not bool(requested_run_id),
                },
            )
        else:
            plan = await self.planner.plan(objective, trace_metadata=metadata)
            state = CompactRunState(objective=objective, plan=plan)
            _replace_runtime_state_message(messages, state)
            await checkpoint_agent_run(
                runtime_run_id=runtime_run_id,
                objective=objective,
                metadata=metadata,
                state=state.as_dict(),
                event_type="run.started",
                lifecycle_state="running",
                payload={"plan": state.plan.as_dict() if state.plan else None},
            )

        async def observe(
            capability_id: str,
            arguments: dict[str, Any],
            observation: dict[str, Any],
        ) -> None:
            state.record_observation(capability_id, observation)
            await checkpoint_agent_run(
                runtime_run_id=runtime_run_id,
                objective=objective,
                metadata=metadata,
                state=state.as_dict(),
                event_type="capability.observed",
                lifecycle_state=(
                    "waiting_approval"
                    if str(observation.get("status") or "").upper() == "WAITING_APPROVAL"
                    else "running"
                ),
                payload={
                    "capability_id": capability_id,
                    "argument_keys": sorted(arguments),
                    "observation": observation,
                },
            )
            if on_observation is not None:
                await _resolve(on_observation(capability_id, arguments, observation))

        attempts: list[dict[str, Any]] = []
        combined_trace = []
        replans = 0

        try:
            while True:
                result = await AgentRuntime(max_steps=max_steps).run(
                    model=model,
                    messages=messages,
                    schemas=schemas,
                    invoke=invoke,
                    on_observation=observe,
                    inference_metadata={
                        **metadata,
                        "plan_revision": state.plan.revision if state.plan else 0,
                        "resumed": resumed,
                    },
                )
                combined_trace.extend(result.get("trace") or [])

                goal_verification = None
                truth = result.get("execution_truth") if isinstance(result.get("execution_truth"), dict) else {}
                waiting_approval = str(truth.get("status") or "").upper() == "WAITING_APPROVAL"
                if (
                    state.plan is not None
                    and state.plan.planning_required
                    and not bool(result.get("stopped"))
                    and not waiting_approval
                ):
                    goal_verification = await self.verifier.verify(
                        objective=objective,
                        success_criteria=state.plan.success_criteria,
                        trace=combined_trace,
                        metadata=metadata,
                    )
                    result["goal_verification"] = goal_verification.as_dict()
                    if not goal_verification.satisfied:
                        for item in goal_verification.missing:
                            marker = f"root objective unverified: {item}"
                            if marker not in state.failures:
                                state.failures.append(marker)
                        result["execution_truth"] = {
                            "status": "UNVERIFIED",
                            "completed": False,
                            "verified": False,
                            "capability_id": "root_objective",
                            "missing": list(goal_verification.missing),
                        }

                attempts.append(
                    {
                        "plan_revision": state.plan.revision if state.plan else 0,
                        "stop_reason": result.get("stop_reason"),
                        "stopped": bool(result.get("stopped")),
                        "execution_truth": result.get("execution_truth"),
                        "goal_verification": goal_verification.as_dict() if goal_verification else None,
                        "budget": result.get("budget") or {},
                    }
                )

                reason = _replan_reason(result, state)
                if not reason or replans >= self.max_replans:
                    if goal_verification is not None and not goal_verification.satisfied:
                        result["message"] = partial_completion_message(goal_verification)
                    result["trace"] = combined_trace
                    result["run_plan"] = state.plan.as_dict() if state.plan else None
                    result["run_state"] = state.as_dict()
                    result["controller_attempts"] = attempts
                    result["replans"] = replans
                    result["runtime_run_id"] = runtime_run_id
                    result["resumed"] = resumed
                    lifecycle = _lifecycle_state(result)
                    await checkpoint_agent_run(
                        runtime_run_id=runtime_run_id,
                        objective=objective,
                        metadata=metadata,
                        state=state.as_dict(),
                        event_type="run.finished",
                        lifecycle_state=lifecycle,
                        payload={
                            "execution_truth": result.get("execution_truth"),
                            "goal_verification": result.get("goal_verification"),
                            "stop_reason": result.get("stop_reason"),
                            "stopped": bool(result.get("stopped")),
                            "replans": replans,
                            "budget": result.get("budget") or {},
                            "resumed": resumed,
                        },
                        error=(
                            str(result.get("stop_reason") or "run failed")
                            if lifecycle == "failed"
                            else None
                        ),
                    )
                    return result

                state.plan = await self.planner.replan(
                    state,
                    reason=reason,
                    trace_metadata=metadata,
                )
                replans += 1
                _replace_runtime_state_message(messages, state)
                await checkpoint_agent_run(
                    runtime_run_id=runtime_run_id,
                    objective=objective,
                    metadata=metadata,
                    state=state.as_dict(),
                    event_type="run.replanned",
                    lifecycle_state="running",
                    payload={"reason": reason, "replans": replans, "plan": state.plan.as_dict()},
                )
        except Exception as error:
            await checkpoint_agent_run(
                runtime_run_id=runtime_run_id,
                objective=objective,
                metadata=metadata,
                state=state.as_dict(),
                event_type="run.failed",
                lifecycle_state="failed",
                payload={"error_type": type(error).__name__, "resumed": resumed},
                error=str(error),
            )
            raise