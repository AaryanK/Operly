"""Adaptive long-horizon controller above the generic AgentRuntime micro-loop."""
from __future__ import annotations

import inspect
import json
from typing import Any, Awaitable, Callable
from uuid import uuid4

from packages.agents.planning import AdaptivePlanner
from packages.agents.run_state import CompactRunState
from packages.agents.runtime import AgentRuntime, ObservationHook


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


class AgentRunController:
    """Plan only when useful, run the micro-loop, compact state, and replan on evidence.

    The controller owns operational state rather than hidden model reasoning. It does
    not widen authority: capability/context access remains entirely in the supplied
    harness callbacks and canonical firewall.
    """

    def __init__(
        self,
        *,
        planner: AdaptivePlanner | None = None,
        max_replans: int = 1,
    ) -> None:
        self.planner = planner or AdaptivePlanner()
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
        runtime_run_id = str(metadata.get("runtime_run_id") or uuid4())
        metadata["runtime_run_id"] = runtime_run_id
        metadata["runtime_controller"] = "adaptive"

        plan = await self.planner.plan(objective, trace_metadata=metadata)
        state = CompactRunState(objective=objective, plan=plan)
        _replace_runtime_state_message(messages, state)

        async def observe(
            capability_id: str,
            arguments: dict[str, Any],
            observation: dict[str, Any],
        ) -> None:
            state.record_observation(capability_id, observation)
            if on_observation is not None:
                # Preserve AgentRuntime's observation-hook contract exactly. The
                # controller may summarize state, but it must not erase caller data.
                await _resolve(on_observation(capability_id, arguments, observation))

        attempts: list[dict[str, Any]] = []
        combined_trace = []
        replans = 0

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
                },
            )
            combined_trace.extend(result.get("trace") or [])
            attempts.append(
                {
                    "plan_revision": state.plan.revision if state.plan else 0,
                    "stop_reason": result.get("stop_reason"),
                    "stopped": bool(result.get("stopped")),
                    "execution_truth": result.get("execution_truth"),
                    "budget": result.get("budget") or {},
                }
            )

            reason = _replan_reason(result, state)
            if not reason or replans >= self.max_replans:
                result["trace"] = combined_trace
                result["run_plan"] = state.plan.as_dict() if state.plan else None
                result["run_state"] = state.as_dict()
                result["controller_attempts"] = attempts
                result["replans"] = replans
                result["runtime_run_id"] = runtime_run_id
                return result

            state.plan = await self.planner.replan(
                state,
                reason=reason,
                trace_metadata=metadata,
            )
            replans += 1
            _replace_runtime_state_message(messages, state)