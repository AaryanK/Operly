"""Async-safe correlation and provider-wire telemetry for model-runtime calls.

AgentRuntime binds conversation/run metadata once. Nested routing/capability model
calls inherit that context. Provider adapters can emit their final normalized wire
body through the sink contract without importing database/application modules.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
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
