"""Generic model/capability loop shared by Operly agent surfaces."""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from packages.model_runtime import InferenceBudget, InferenceRequest


SchemaLoader = Callable[[], Awaitable[list[dict[str, Any]]] | list[dict[str, Any]]]
CapabilityInvoker = Callable[
    [str, dict[str, Any], str | None],
    Awaitable[dict[str, Any]] | dict[str, Any],
]
ObservationHook = Callable[
    [str, dict[str, Any], dict[str, Any]],
    Awaitable[None] | None,
]


@dataclass(frozen=True, slots=True)
class AgentTraceEntry:
    capability_id: str
    arguments: dict[str, Any]
    observation: dict[str, Any]
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentExecutionBudget:
    """Adaptive agent budget with a bounded, observable extension policy."""

    base_steps: int = 8
    max_steps: int = 24
    extension_steps: int = 4
    max_tool_calls: int = 64

    def normalized(self) -> "AgentExecutionBudget":
        base = max(1, int(self.base_steps))
        hard = max(base, int(self.max_steps))
        return AgentExecutionBudget(
            base_steps=base,
            max_steps=hard,
            extension_steps=max(1, int(self.extension_steps)),
            max_tool_calls=max(1, int(self.max_tool_calls)),
        )


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


def _made_progress(observation: dict[str, Any]) -> bool:
    if observation.get("ok") is True or observation.get("success") is True:
        return True
    status = str(observation.get("status") or "").lower()
    return status in {"completed", "success", "verified", "waiting_approval", "awaiting_approval"}


class AgentRuntime:
    """Stable orchestration loop over Model + capability callbacks.

    ``max_steps`` is retained as the compatibility name for the initial budget,
    not a hard abort. Productive work can extend up to the bounded hard ceiling.
    """

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
                max_steps=max(self.max_steps + 4, self.max_steps * 3),
            )
        ).normalized()

    async def _infer(
        self,
        model,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        infer = getattr(model, "infer", None)
        if callable(infer):
            result = await infer(
                InferenceRequest(
                    messages=tuple(messages),
                    tools=tuple(tools),
                    budget=self.inference_budget,
                    metadata=dict(metadata or {}),
                )
            )
            return dict(result.message)

        chat = getattr(model, "chat", None)
        if callable(chat):
            return dict(await chat(messages, tools))
        raise TypeError("AgentRuntime requires a Model.infer-compatible object")

    @staticmethod
    def _arguments(call: dict[str, Any]) -> tuple[str, dict[str, Any], str | None]:
        function = call.get("function") or {}
        name = str(function.get("name") or "").strip()
        raw = function.get("arguments") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        arguments = raw if isinstance(raw, dict) else {}
        call_id = str(call.get("id") or "").strip() or None
        return name, arguments, call_id

    async def run(
        self,
        *,
        model,
        messages: list[dict[str, Any]],
        schemas: SchemaLoader,
        invoke: CapabilityInvoker,
        on_observation: ObservationHook | None = None,
        inference_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trace: list[AgentTraceEntry] = []
        budget = self.execution_budget
        allowed_steps = budget.base_steps
        steps_used = 0
        tool_calls = 0
        extensions = 0
        last_step_progress = False

        while steps_used < allowed_steps and steps_used < budget.max_steps:
            tools = list(await _resolve(schemas()) or [])
            message = await self._infer(
                model,
                messages,
                tools,
                metadata=inference_metadata,
            )
            messages.append(message)
            steps_used += 1
            calls = message.get("tool_calls") or []
            if not calls:
                return {
                    "message": message.get("content") or "Done.",
                    "trace": trace,
                    "messages": messages,
                    "stopped": False,
                    "stop_reason": "completed",
                    "budget": {
                        "stepsUsed": steps_used,
                        "stepsAllowed": allowed_steps,
                        "hardStepLimit": budget.max_steps,
                        "toolCalls": tool_calls,
                        "maxToolCalls": budget.max_tool_calls,
                        "extensions": extensions,
                    },
                }

            last_step_progress = False
            for call in calls:
                if tool_calls >= budget.max_tool_calls:
                    break
                if not isinstance(call, dict):
                    continue
                name, arguments, call_id = self._arguments(call)
                if not name:
                    observation = {"ok": False, "error": "Model requested an unnamed capability"}
                else:
                    observation = dict(
                        await _resolve(invoke(name, dict(arguments), call_id)) or {}
                    )
                tool_calls += 1
                last_step_progress = last_step_progress or _made_progress(observation)
                entry = AgentTraceEntry(
                    capability_id=name,
                    arguments=dict(arguments),
                    observation=observation,
                    call_id=call_id,
                )
                trace.append(entry)
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": name,
                        "content": json.dumps(observation, ensure_ascii=False, default=str),
                    }
                )
                if on_observation is not None:
                    await _resolve(on_observation(name, arguments, observation))

            if tool_calls >= budget.max_tool_calls:
                break

            # A productive run is allowed to continue, but only inside the hard
            # ceiling. Repeated failing/no-op turns do not earn more budget.
            if steps_used >= allowed_steps and last_step_progress and allowed_steps < budget.max_steps:
                new_allowed = min(budget.max_steps, allowed_steps + budget.extension_steps)
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
                "Stopped after exhausting the bounded execution budget. "
                "The run trace includes the exact stop reason and can be resumed safely."
            ),
            "trace": trace,
            "messages": messages,
            "stopped": True,
            "stop_reason": stop_reason,
            "budget": {
                "stepsUsed": steps_used,
                "stepsAllowed": allowed_steps,
                "hardStepLimit": budget.max_steps,
                "toolCalls": tool_calls,
                "maxToolCalls": budget.max_tool_calls,
                "extensions": extensions,
                "lastStepMadeProgress": last_step_progress,
            },
        }
