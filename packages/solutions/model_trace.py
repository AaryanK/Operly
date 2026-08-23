"""Run-scoped model request/response and provider-attempt tracing for Solution jobs."""
from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

from packages.model_runtime import register_model_telemetry_sink
from packages.model_runtime.registry import ModelAttemptEvent
from packages.studio.model_trace import redact_trace_value

_MAX_TRACE_JSON_CHARS = 500_000


@dataclass
class SolutionModelTraceScope:
    job_id: str
    call_index: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)


_SCOPE: ContextVar[SolutionModelTraceScope | None] = ContextVar(
    "operly_solution_model_trace",
    default=None,
)
_INSTALLED = False


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _bounded(value: Any) -> Any:
    redacted = redact_trace_value(value)
    encoded = _canonical(redacted)
    if len(encoded) <= _MAX_TRACE_JSON_CHARS:
        return redacted
    return {
        "truncated": True,
        "originalJsonChars": len(encoded),
        "digest": _digest(value),
        "notice": "Trace exceeded the durable Solution-job payload limit.",
    }


def _model_candidates(model: Any) -> list[dict[str, Any]]:
    candidates = getattr(model, "models", None) or (model,)
    rows = []
    for candidate in candidates:
        rows.append(
            {
                "resourceId": str(getattr(candidate, "id", "unknown")),
                "provider": str(getattr(candidate, "provider", "unknown")),
                "providerModelId": str(
                    getattr(
                        candidate,
                        "provider_model_id",
                        getattr(candidate, "id", "unknown"),
                    )
                ),
            }
        )
    return rows


async def telemetry_sink(event: ModelAttemptEvent) -> None:
    scope = _SCOPE.get()
    if scope is None:
        return
    scope.attempts.append(
        {
            "phase": event.phase,
            "resourceId": event.resource_id,
            "provider": event.provider,
            "modelId": event.provider_model_id,
            "attempt": event.attempt,
            "latencyMs": event.latency_ms,
            "classification": event.classification,
            "retryable": event.retryable,
            "detail": str(event.detail or "")[:500] or None,
        }
    )


def install_telemetry() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    register_model_telemetry_sink(telemetry_sink)
    _INSTALLED = True


def begin(job_id: str):
    install_telemetry()
    return _SCOPE.set(SolutionModelTraceScope(job_id=job_id))


def snapshot() -> dict[str, Any]:
    scope = _SCOPE.get()
    if scope is None:
        return {"aiInvoked": False, "modelCalls": [], "modelAttempts": []}
    return {
        "aiInvoked": bool(scope.calls or scope.attempts),
        "modelCalls": list(scope.calls),
        "modelAttempts": list(scope.attempts),
    }


def end(token) -> dict[str, Any]:
    data = snapshot()
    _SCOPE.reset(token)
    return data


class TracingModelChatClient:
    """Provider-neutral trace wrapper around the shared ModelChatAdapter."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    @property
    def model(self) -> Any:
        return getattr(self.inner, "model", None)

    @property
    def last_model(self) -> str:
        return str(
            getattr(
                self.inner,
                "last_model",
                getattr(getattr(self.inner, "model", None), "id", "unknown"),
            )
        )

    async def chat(self, messages: list[dict[str, Any]], tools=None) -> dict[str, Any]:
        scope = _SCOPE.get()
        if scope is None:
            return await self.inner.chat(messages, tools)

        scope.call_index += 1
        call_index = scope.call_index
        tool_list = list(tools or ())
        model = getattr(self.inner, "model", None)
        budget = getattr(self.inner, "budget", None)
        request_payload = {
            "callIndex": call_index,
            "phase": "request",
            "exactPayloadDigest": _digest({"messages": messages, "tools": tool_list}),
            "candidateModels": _model_candidates(model) if model is not None else [],
            "budget": asdict(budget) if budget is not None and is_dataclass(budget) else None,
            "messages": messages,
            "tools": tool_list,
        }
        scope.calls.append(_bounded(request_payload))

        try:
            message = await self.inner.chat(messages, tool_list)
        except BaseException as error:
            scope.calls.append(
                _bounded(
                    {
                        "callIndex": call_index,
                        "phase": "error",
                        "type": type(error).__name__,
                        "message": str(error),
                        "classification": getattr(error, "classification", None),
                        "retryable": getattr(error, "retryable", None),
                        "provider": getattr(error, "provider", None),
                        "modelId": getattr(error, "model_id", None),
                    }
                )
            )
            raise

        result = getattr(self.inner, "last_result", None)
        usage = getattr(result, "usage", None)
        scope.calls.append(
            _bounded(
                {
                    "callIndex": call_index,
                    "phase": "response",
                    "message": message,
                    "modelResourceId": getattr(result, "model_resource_id", None),
                    "provider": getattr(result, "provider", None),
                    "providerModelId": getattr(result, "provider_model_id", None),
                    "latencyMs": getattr(result, "latency_ms", None),
                    "finishReason": getattr(result, "finish_reason", None),
                    "attempt": getattr(result, "attempt", None),
                    "usage": asdict(usage) if usage is not None and is_dataclass(usage) else None,
                }
            )
        )
        return message


def trace_client(client: Any) -> Any:
    return TracingModelChatClient(client) if _SCOPE.get() is not None else client
