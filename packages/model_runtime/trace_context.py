"""Async-safe correlation metadata for nested model-runtime calls.

AgentRuntime binds conversation/run metadata once. Any model invocation triggered
inside routing or a capability inherits that context without coupling provider
adapters to application services.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Mapping

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
