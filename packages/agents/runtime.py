"""Generic model/capability loop shared by Operly agent surfaces."""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import uuid4

from packages.database.model_trace import ensure_model_trace_sink
from packages.database.runtime_trace_events import emit_runtime_trace_event
from packages.model_runtime import InferenceBudget, InferenceRequest
from packages.model_runtime.conversation_policy import is_trivial_conversation
from packages.model_runtime.trace_context import runtime_trace_scope
from packages.model_runtime.trace_events import RuntimeTraceEvent


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


class _DuplicateProperty(ValueError):
    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


def _strict_json_object(raw: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Parse model-authored JSON without silently accepting duplicate keys."""

    def pairs_hook(pairs):
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise _DuplicateProperty(str(key))
            value[str(key)] = item
        return value

    try:
        parsed = json.loads(raw, object_pairs_hook=pairs_hook)
    except _DuplicateProperty as error:
        return {}, [{"path": error.key, "reason": "duplicate property"}]
    except json.JSONDecodeError as error:
        return {}, [{"path": "$", "reason": f"invalid JSON: {error.msg}"}]
    if not isinstance(parsed, dict):
        return {}, [{"path": "$", "reason": "arguments must be a JSON object"}]
    return parsed, []


def _made_progress(observation: dict[str, Any]) -> bool:
    if observation.get("ok") is True or observation.get("success") is True:
        return True
    status = str(observation.get("status") or "").lower()
    return status in {
        "completed",
        "success",
        "verified",
        "waiting_approval",
        "awaiting_approval",
        "running",
    }


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if str(message.get("role") or "") == "user":
            return str(message.get("content") or "")
    return ""


def _ensure_tool_call_ids(message: dict[str, Any]) -> None:
    """Give provider-neutral tool calls a stable correlation id when absent.

    Some providers (notably native Ollama) do not emit call ids, while other
    providers require one when a tool result is replayed. The shared runtime owns
    this correlation concern: native ids are preserved and only missing ids receive
    an Operly-generated value. Provider adapters remain free to drop the id if their
    wire protocol does not use it.
    """
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return
    for call in calls:
        if not isinstance(call, dict):
            continue
        if not str(call.get("id") or "").strip():
            call["id"] = f"operly-call-{uuid4()}"


def _tool_ids(tools: list[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        name = str(function.get("name") or "").strip()
        if name:
            output.append(name)
    return output


def _execution_truth(trace: list[AgentTraceEntry]) -> dict[str, Any] | None:
    """Derive final user-facing execution truth from actual capability results.

    Pending approvals remain unresolved even if the model performs additional read
    operations afterwards. Other failure/running states guard the final response
    only when they are the latest action-lifecycle result, allowing a later verified
    retry to truthfully supersede an earlier failed attempt.
    """
    lifecycle_states = {
        "WAITING_APPROVAL",
        "RUNNING",
        "VERIFIED",
        "FAILED",
        "UNVERIFIED",
        "CANCELLED",
        "EXPIRED",
    }
    pending = [
        entry
        for entry in trace
        if str(entry.observation.get("status") or "").upper() == "WAITING_APPROVAL"
    ]
    if pending:
        entry = pending[-1]
        return {
            "status": "WAITING_APPROVAL",
            "completed": False,
            "verified": False,
            "capability_id": entry.capability_id,
            "action_id": entry.observation.get("action_id"),
            "approval_id": entry.observation.get("approval_id"),
        }

    for entry in reversed(trace):
        status = str(entry.observation.get("status") or "").upper()
        if status not in lifecycle_states:
            continue
        lifecycle = entry.observation.get("lifecycle")
        completed = (
            bool(lifecycle.get("completed"))
            if isinstance(lifecycle, dict)
            else status == "VERIFIED"
        )
        verified = (
            bool(lifecycle.get("verified"))
            if isinstance(lifecycle, dict)
            else status == "VERIFIED"
        )
        return {
            "status": status,
            "completed": completed,
            "verified": verified,
            "capability_id": entry.capability_id,
            "action_id": entry.observation.get("action_id"),
            "approval_id": entry.observation.get("approval_id"),
        }
    return None


def _truthful_final_message(model_message: str, truth: dict[str, Any] | None) -> str:
    """Prevent model prose from claiming a lifecycle transition that did not occur."""
    if not truth or truth.get("verified") is True:
        return model_message or "Done."

    status = str(truth.get("status") or "").upper()
    capability = str(truth.get("capability_id") or "the operation")
    if status == "WAITING_APPROVAL":
        return (
            f"Approval is required before {capability} can run. "
            "The approval-gated operation has not been completed yet."
        )
    if status == "RUNNING":
        return f"{capability} is still running and has not been verified complete."
    if status == "FAILED":
        return f"{capability} failed and was not completed."
    if status == "UNVERIFIED":
        return f"{capability} ran, but Operly could not verify that it completed successfully."
    if status == "CANCELLED":
        return f"{capability} was cancelled and was not completed."
    if status == "EXPIRED":
        return f"{capability} expired and was not completed."
    return model_message or "Done."


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
        # Progressive tool exposure starts at zero for unmistakable conversation.
        # This is enforced at the final model boundary so a greeting cannot call
        # capability.search even if the harness has already prepared schemas.
        effective_tools = [] if is_trivial_conversation(_last_user_text(messages)) else tools
        infer = getattr(model, "infer", None)
        if callable(infer):
            result = await infer(
                InferenceRequest(
                    messages=tuple(messages),
                    tools=tuple(effective_tools),
                    budget=self.inference_budget,
                    metadata=dict(metadata or {}),
                )
            )
            return dict(result.message)

        chat = getattr(model, "chat", None)
        if callable(chat):
            return dict(await chat(messages, effective_tools))
        raise TypeError("AgentRuntime requires a Model.infer-compatible object")

    @staticmethod
    def _arguments_checked(
        call: dict[str, Any],
    ) -> tuple[str, dict[str, Any], str | None, list[dict[str, str]]]:
        function = call.get("function") or {}
        name = str(function.get("name") or "").strip()
        raw = function.get("arguments")
        errors: list[dict[str, str]] = []
        if raw is None:
            arguments: dict[str, Any] = {}
        elif isinstance(raw, str):
            arguments, errors = _strict_json_object(raw)
        elif isinstance(raw, dict):
            arguments = dict(raw)
        else:
            arguments = {}
            errors = [{"path": "$", "reason": "arguments must be a JSON object"}]
        call_id = str(call.get("id") or "").strip() or None
        return name, arguments, call_id, errors

    @staticmethod
    def _arguments(call: dict[str, Any]) -> tuple[str, dict[str, Any], str | None]:
        """Compatibility accessor retained for callers/tests outside the loop."""
        name, arguments, call_id, _ = AgentRuntime._arguments_checked(call)
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
        ensure_model_trace_sink()
        trace: list[AgentTraceEntry] = []
        budget = self.execution_budget
        allowed_steps = budget.base_steps
        steps_used = 0
        tool_calls = 0
        extensions = 0
        last_step_progress = False
        model_call_index = 0
        run_metadata = dict(inference_metadata or {})
        runtime_run_id = str(run_metadata.get("runtime_run_id") or uuid4())
        run_metadata["runtime_run_id"] = runtime_run_id

        with runtime_trace_scope(run_metadata):
            while steps_used < allowed_steps and steps_used < budget.max_steps:
                tools = list(await _resolve(schemas()) or [])
                model_call_index += 1
                model_metadata = {
                    **run_metadata,
                    "runtime_component": "agent",
                    "runtime_step": model_call_index,
                }
                suppressed = is_trivial_conversation(_last_user_text(messages))
                with runtime_trace_scope(model_metadata):
                    await emit_runtime_trace_event(
                        RuntimeTraceEvent.MODEL_REQUEST,
                        {
                            "message_count": len(messages),
                            "candidate_tool_count": len(tools),
                            "candidate_tool_ids": _tool_ids(tools),
                            "tool_surface_suppressed": suppressed,
                        },
                        metadata=model_metadata,
                        component="agent",
                        step=model_call_index,
                    )
                    message = await self._infer(model, messages, tools, metadata=model_metadata)
                    await emit_runtime_trace_event(
                        RuntimeTraceEvent.MODEL_RESPONSE,
                        {
                            "has_content": bool(str(message.get("content") or "").strip()),
                            "tool_call_count": len(message.get("tool_calls") or []),
                        },
                        metadata=model_metadata,
                        component="agent",
                        step=model_call_index,
                    )
                _ensure_tool_call_ids(message)
                messages.append(message)
                steps_used += 1
                calls = message.get("tool_calls") or []
                if not calls:
                    truth = _execution_truth(trace)
                    return {
                        "message": _truthful_final_message(
                            str(message.get("content") or ""),
                            truth,
                        ),
                        "execution_truth": truth,
                        "trace": trace,
                        "messages": messages,
                        "stopped": False,
                        "stop_reason": "completed",
                        "runtime_run_id": runtime_run_id,
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
                    name, arguments, call_id, argument_errors = self._arguments_checked(call)
                    capability_metadata = {
                        **run_metadata,
                        "runtime_component": f"capability:{name or 'unknown'}",
                        "runtime_step": model_call_index,
                        "capability_id": name or None,
                        "tool_call_id": call_id,
                    }
                    with runtime_trace_scope(capability_metadata):
                        await emit_runtime_trace_event(
                            RuntimeTraceEvent.CAPABILITY_REQUESTED,
                            {
                                "capability_id": name or None,
                                "call_id": call_id,
                                "argument_keys": sorted(arguments),
                                "argument_parse_errors": list(argument_errors),
                            },
                            metadata=capability_metadata,
                            component=f"capability:{name or 'unknown'}",
                            step=model_call_index,
                            resource_id=name or "unnamed-capability",
                        )
                        if not name:
                            observation = {
                                "ok": False,
                                "status": "INVALID_ARGUMENTS",
                                "error": "Model requested an unnamed capability",
                                "errors": [{"path": "function.name", "reason": "required"}],
                                "retryable": True,
                            }
                        elif argument_errors:
                            observation = {
                                "ok": False,
                                "status": "INVALID_ARGUMENTS",
                                "error": "Capability arguments are not valid JSON",
                                "errors": argument_errors,
                                "retryable": True,
                            }
                        else:
                            observation = dict(await _resolve(invoke(name, dict(arguments), call_id)) or {})
                        if str(observation.get("status") or "").upper() in {
                            "INVALID_ARGUMENTS",
                            "DENIED",
                        }:
                            await emit_runtime_trace_event(
                                RuntimeTraceEvent.CAPABILITY_REJECTED,
                                {
                                    "capability_id": name or None,
                                    "call_id": call_id,
                                    "status": observation.get("status"),
                                    "error": observation.get("error"),
                                    "errors": observation.get("errors") or [],
                                },
                                metadata=capability_metadata,
                                component=f"capability:{name or 'unknown'}",
                                step=model_call_index,
                                resource_id=name or "unnamed-capability",
                                classification=str(observation.get("status") or "").lower() or None,
                                retryable=bool(observation.get("retryable")),
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
                    tool_message = {
                        "role": "tool",
                        "tool_name": name,
                        "content": json.dumps(observation, ensure_ascii=False, default=str),
                    }
                    if call_id:
                        tool_message["tool_call_id"] = call_id
                    messages.append(tool_message)
                    if on_observation is not None:
                        await _resolve(on_observation(name, arguments, observation))

                if tool_calls >= budget.max_tool_calls:
                    break

                if steps_used >= allowed_steps and last_step_progress and allowed_steps < budget.max_steps:
                    new_allowed = min(budget.max_steps, allowed_steps + budget.extension_steps)
                    if new_allowed > allowed_steps:
                        allowed_steps = new_allowed
                        extensions += 1

        stop_reason = "tool_call_budget_exhausted" if tool_calls >= budget.max_tool_calls else "execution_budget_exhausted"
        return {
            "message": (
                "Stopped after exhausting the bounded execution budget. "
                "The run trace includes the exact stop reason and can be resumed safely."
            ),
            "execution_truth": _execution_truth(trace),
            "trace": trace,
            "messages": messages,
            "stopped": True,
            "stop_reason": stop_reason,
            "runtime_run_id": runtime_run_id,
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
