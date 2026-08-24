"""Best-effort persistence for non-model runtime trace events.

Model request/response wire packets already have dedicated telemetry sinks. This
module records the surrounding orchestration lifecycle in the same conversation
trace so AI Debug can explain routing, capability, approval, connector, delivery,
and workflow state transitions end to end.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from packages.database.db import SessionFactory
from packages.database.model_trace import _trace_row
from packages.model_runtime.trace_context import current_trace_metadata
from packages.model_runtime.trace_events import RuntimeTraceEvent, TRACE_EVENT_VALUES


async def emit_runtime_trace_event(
    event_type: RuntimeTraceEvent | str,
    payload: dict[str, Any] | None = None,
    *,
    metadata: dict[str, Any] | None = None,
    component: str | None = None,
    step: int | None = None,
    resource_id: str | None = None,
    classification: str | None = None,
    retryable: bool | None = None,
) -> None:
    """Append one orchestration event when a conversation/run trace is available.

    Tracing is intentionally best-effort: inability to persist debug telemetry may
    never break capability execution or delivery.
    """
    value = str(getattr(event_type, "value", event_type) or "").strip()
    if value not in TRACE_EVENT_VALUES:
        return

    trace_metadata = current_trace_metadata()
    trace_metadata.update(dict(metadata or {}))
    if component:
        trace_metadata["runtime_component"] = component
    if step is not None:
        trace_metadata["runtime_step"] = int(step)

    conversation_id = str(trace_metadata.get("conversation_id") or "").strip()
    run_id = str(trace_metadata.get("runtime_run_id") or "").strip()
    if not conversation_id or not run_id:
        return

    packet = {
        "eventType": value,
        "payload": dict(payload or {}),
        "metadata": trace_metadata,
    }
    attempt_id = str(uuid4())
    try:
        row = _trace_row(
            metadata=trace_metadata,
            run_id=run_id,
            conversation_id=conversation_id,
            attempt_id=attempt_id,
            phase=value,
            resource_id=(resource_id or value),
            provider="operly",
            provider_model_id="runtime",
            payload=packet,
            classification=classification,
            retryable=retryable,
        )
        async with SessionFactory() as db:
            db.add(row)
            await db.commit()
    except Exception:
        return
