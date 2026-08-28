"""Factory-specific reason-act-observe runtime with a resettable working set.

The generic AgentRuntime intentionally preserves a conversation across tool rounds. That
is useful for interactive agents but wasteful for disposable Factory stations because a
stage ContextCapsule may contain materialized workspace context. This wrapper executes
one bounded AgentRuntime turn at a time, preserves the raw trace, and rebuilds the
model-visible working set between turns.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, Awaitable

from packages.agents.runtime import AgentExecutionBudget, AgentRuntime
from packages.model_runtime import InferenceBudget


SchemaLoader = Callable[[], Awaitable[list[dict[str, Any]]] | list[dict[str, Any]]]
CapabilityInvoker = Callable[
    [str, dict[str, Any], str | None],
    Awaitable[dict[str, Any]] | dict[str, Any],
]
WorkingSetReducer = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


def _observation_payloads(entry: Any) -> list[dict[str, Any]]:
    observation = getattr(entry, "observation", {})
    if not isinstance(observation, dict):
        return []
    payloads = [observation]
    nested = observation.get("observation")
    if isinstance(nested, dict):
        payloads.append(nested)
    return payloads


def _round_made_progress(trace: list[Any]) -> bool:
    for entry in trace:
        for observation in _observation_payloads(entry):
            if observation.get("ok") is True or observation.get("success") is True:
                return True
            if str(observation.get("status") or "").strip().lower() in {
                "completed",
                "success",
                "verified",
                "waiting_approval",
                "awaiting_approval",
                "running",
            }:
                return True
    return False


def _pause_reason(trace: list[Any]) -> str | None:
    """Return lifecycle states where another model call cannot add useful work."""

    for entry in reversed(trace):
        for observation in _observation_payloads(entry):
            if bool(observation.get("deferred")):
                return "waiting_external"
            status = str(observation.get("status") or "").strip().lower()
            if status in {"waiting_approval", "awaiting_approval"}:
                return "waiting_approval"
            if status in {
                "rejected",
                "denied",
                "cancelled",
                "expired",
                "failed",
                "verification_failed",
                "unverified",
            }:
                return status
    return None


class FactoryStageRuntime:
    """Run one disposable factory stage without cumulative transcript replay."""

    def __init__(
        self,
        *,
        max_steps: int = 8,
        inference_budget: InferenceBudget | None = None,
        execution_budget: AgentExecutionBudget | None = None,
    ) -> None:
        self.max_steps = max(1, int(max_steps))
        self.inference_budget = inference_budget
        self.execution_budget = (
            execution_budget
            or AgentExecutionBudget(
                base_steps=self.max_steps,
                max_steps=max(self.max_steps + 2, self.max_steps),
                extension_steps=2,
                max_tool_calls=24,
            )
        ).normalized()

    async def run(
        self,
        *,
        model,
        messages: list[dict[str, Any]],
        schemas: SchemaLoader,
        invoke: CapabilityInvoker,
        inference_metadata: dict[str, Any] | None = None,
        reduce_working_messages: WorkingSetReducer | None = None,
    ) -> dict[str, Any]:
        budget = self.execution_budget
        working_messages = [dict(message) for message in messages]
        aggregate_trace: list[Any] = []
        latest_truth: dict[str, Any] | None = None
        steps_used = 0
        tool_calls = 0
        allowed_steps = budget.base_steps
        extensions = 0
        working_set_resets = 0
        discarded_working_messages = 0
        last_step_progress = False
        last_result: dict[str, Any] | None = None
        metadata = dict(inference_metadata or {})

        while steps_used < allowed_steps and steps_used < budget.max_steps:
            remaining_tool_calls = max(1, budget.max_tool_calls - tool_calls)
            micro_runtime = AgentRuntime(
                max_steps=1,
                execution_budget=AgentExecutionBudget(
                    base_steps=1,
                    max_steps=1,
                    extension_steps=1,
                    max_tool_calls=remaining_tool_calls,
                ),
                inference_budget=self.inference_budget,
            )
            round_result = await micro_runtime.run(
                model=model,
                messages=[dict(message) for message in working_messages],
                schemas=schemas,
                invoke=invoke,
                inference_metadata={
                    **metadata,
                    "runtime_component": "factory_stage_turn",
                    "factory_stage_turn": steps_used + 1,
                },
            )
            last_result = round_result
            steps_used += 1

            round_trace = list(round_result.get("trace") or [])
            aggregate_trace.extend(round_trace)
            tool_calls += len(round_trace)
            truth = round_result.get("execution_truth")
            if isinstance(truth, dict):
                latest_truth = dict(truth)
            last_step_progress = _round_made_progress(round_trace)

            # A model response without tool calls is the stage's final reasoning turn.
            if not bool(round_result.get("stopped")):
                merged = dict(round_result)
                merged["trace"] = aggregate_trace
                merged["execution_truth"] = latest_truth
                merged["budget"] = {
                    "stepsUsed": steps_used,
                    "stepsAllowed": allowed_steps,
                    "hardStepLimit": budget.max_steps,
                    "toolCalls": tool_calls,
                    "maxToolCalls": budget.max_tool_calls,
                    "extensions": extensions,
                    "workingSetResets": working_set_resets,
                    "discardedWorkingMessages": discarded_working_messages,
                }
                return merged

            # One-step AgentRuntime reports its intentional per-turn boundary as an
            # execution-budget stop. With a capability trace that means "observe then
            # continue", not a failed Factory stage.
            if not round_trace:
                break

            pause_reason = _pause_reason(round_trace)
            if pause_reason is not None:
                return {
                    "message": (
                        "Stage paused after the latest capability result; durable evidence "
                        "is preserved in the Factory trace."
                    ),
                    "execution_truth": latest_truth,
                    "trace": aggregate_trace,
                    "messages": list(round_result.get("messages") or working_messages),
                    "stopped": False,
                    "stop_reason": pause_reason,
                    "runtime_run_id": round_result.get("runtime_run_id"),
                    "budget": {
                        "stepsUsed": steps_used,
                        "stepsAllowed": allowed_steps,
                        "hardStepLimit": budget.max_steps,
                        "toolCalls": tool_calls,
                        "maxToolCalls": budget.max_tool_calls,
                        "extensions": extensions,
                        "workingSetResets": working_set_resets,
                        "discardedWorkingMessages": discarded_working_messages,
                    },
                }

            if tool_calls >= budget.max_tool_calls:
                break

            round_messages = list(round_result.get("messages") or working_messages)
            if reduce_working_messages is not None:
                reduced = list(await _resolve(reduce_working_messages(round_messages)) or [])
                if reduced:
                    discarded_working_messages += max(0, len(round_messages) - len(reduced))
                    working_messages = [dict(message) for message in reduced]
                    working_set_resets += 1
                else:
                    working_messages = round_messages
            else:
                working_messages = round_messages

            if (
                steps_used >= allowed_steps
                and last_step_progress
                and allowed_steps < budget.max_steps
            ):
                new_allowed = min(
                    budget.max_steps,
                    allowed_steps + budget.extension_steps,
                )
                if new_allowed > allowed_steps:
                    allowed_steps = new_allowed
                    extensions += 1

        stop_reason = (
            "tool_call_budget_exhausted"
            if tool_calls >= budget.max_tool_calls
            else "execution_budget_exhausted"
        )
        return {
            "message": (
                "Stopped after exhausting the bounded Factory stage budget. Raw capability "
                "evidence is preserved and the control plane may repair or resume safely."
            ),
            "execution_truth": latest_truth,
            "trace": aggregate_trace,
            "messages": (
                list(last_result.get("messages") or working_messages)
                if last_result is not None
                else working_messages
            ),
            "stopped": True,
            "stop_reason": stop_reason,
            "runtime_run_id": (
                last_result.get("runtime_run_id") if last_result is not None else None
            ),
            "budget": {
                "stepsUsed": steps_used,
                "stepsAllowed": allowed_steps,
                "hardStepLimit": budget.max_steps,
                "toolCalls": tool_calls,
                "maxToolCalls": budget.max_tool_calls,
                "extensions": extensions,
                "lastStepMadeProgress": last_step_progress,
                "workingSetResets": working_set_resets,
                "discardedWorkingMessages": discarded_working_messages,
            },
        }
