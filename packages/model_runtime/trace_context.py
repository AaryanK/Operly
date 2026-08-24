"""Async-safe correlation and runtime/provider telemetry for model calls.

AgentRuntime or another durable orchestrator binds conversation/run metadata once.
Nested routing/capability model calls inherit that context. Provider adapters and
trusted runtime components can emit sanitized telemetry without importing database
or application modules.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Iterator, Mapping

_TRACE_METADATA: ContextVar[dict[str, Any] | None] = ContextVar(
    "operly_model_runtime_trace_metadata",
    default=None,
)


def current_trace_metadata() -> dict[str, Any]:
    return dict(_TRACE_METADATA.get() or {})


@contextmanager
def runtime_trace_scope(metadata: Mapping[str, Any] | None) -> Iterator[None]:
    merged = current_trace_metadata()
    merged.update(dict(metadata or {}))
    token = _TRACE_METADATA.set(merged)
    try:
        yield
    finally:
        _TRACE_METADATA.reset(token)


@dataclass(frozen=True, slots=True)
class ProviderWireEvent:
    phase: str
    wire_call_id: str
    provider: str
    provider_model_id: str
    payload: Any
    status: int | None = None
    response_metadata: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


ProviderWireTelemetrySink = Callable[
    [ProviderWireEvent], Awaitable[None] | None
]
_PROVIDER_WIRE_SINKS: list[ProviderWireTelemetrySink] = []


def register_provider_wire_telemetry_sink(sink: ProviderWireTelemetrySink) -> None:
    if sink not in _PROVIDER_WIRE_SINKS:
        _PROVIDER_WIRE_SINKS.append(sink)


async def emit_provider_wire_event(event: ProviderWireEvent) -> None:
    """Best-effort provider-wire telemetry; tracing can never break inference."""
    for sink in tuple(_PROVIDER_WIRE_SINKS):
        try:
            result = sink(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            continue


@dataclass(frozen=True, slots=True)
class RuntimeTraceEvent:
    """Provider-neutral trusted-runtime event correlated to the current AI run."""

    event_type: str
    payload: Any = None
    phase: str = "event"
    resource_id: str = "operly.runtime"
    classification: str | None = None
    retryable: bool | None = None
    metadata: dict[str, Any] | None = None


RuntimeTraceTelemetrySink = Callable[
    [RuntimeTraceEvent], Awaitable[None] | None
]
_RUNTIME_TRACE_SINKS: list[RuntimeTraceTelemetrySink] = []


def register_runtime_trace_telemetry_sink(sink: RuntimeTraceTelemetrySink) -> None:
    if sink not in _RUNTIME_TRACE_SINKS:
        _RUNTIME_TRACE_SINKS.append(sink)


async def emit_runtime_trace_event(event: RuntimeTraceEvent) -> None:
    """Emit one sanitized runtime event without allowing tracing to break work."""
    metadata = current_trace_metadata()
    metadata.update(dict(event.metadata or {}))
    correlated = replace(event, metadata=metadata)
    for sink in tuple(_RUNTIME_TRACE_SINKS):
        try:
            result = sink(correlated)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            continue
