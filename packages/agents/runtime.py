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


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class AgentRuntime:
    """Small stable orchestration loop over Model + capability callbacks.

    ``model`` should implement the target ``Model.infer`` contract. A temporary
    ``chat`` fallback remains for deterministic tests and migration-only clients;
    provider-specific clients are never constructed or inspected here.
    """

    def __init__(
        self,
        *,
        max_steps: int = 8,
        inference_budget: InferenceBudget | None = None,
    ) -> None:
        self.max_steps = max(1, int(max_steps))
        self.inference_budget = inference_budget

    async def _infer(
        self,
        model,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        infer = getattr(model, "infer", None)
        if callable(infer):
            result = await infer(
                InferenceRequest(
                    messages=tuple(messages),
                    tools=tuple(tools),
                    budget=self.inference_budget,
                )
            )
            return dict(result.message)

        # Migration/test compatibility only. This keeps legacy fake clients useful
        # while production callers converge on Model.infer().
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
    ) -> dict[str, Any]:
        trace: list[AgentTraceEntry] = []

        for _ in range(self.max_steps):
            tools = list(await _resolve(schemas()) or [])
            message = await self._infer(model, messages, tools)
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                return {
                    "message": message.get("content") or "Done.",
                    "trace": trace,
                    "messages": messages,
                    "stopped": False,
                }

            for call in calls:
                if not isinstance(call, dict):
                    continue
                name, arguments, call_id = self._arguments(call)
                if not name:
                    observation = {"ok": False, "error": "Model requested an unnamed capability"}
                else:
                    observation = dict(
                        await _resolve(invoke(name, dict(arguments), call_id)) or {}
                    )
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

        return {
            "message": "Stopped at the safe capability-call limit.",
            "trace": trace,
            "messages": messages,
            "stopped": True,
        }
