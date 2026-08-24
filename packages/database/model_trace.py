"""Durable conversation-scoped model-runtime tracing.

The sink records runtime-visible request/response evidence and provider diagnostics,
never transport credentials or hidden provider reasoning. Credential-shaped and
reasoning-only values are redacted in the durable payload while a SHA-256 digest
preserves the identity of the exact unredacted packet observed at runtime.

Model-visible payloads are deliberately not truncated here: AI Debug is intended to
answer exactly what the model received. Storage retention is an operational concern
and must not silently alter an individual trace packet.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from datetime import datetime
from typing import Any

from sqlalchemy import select

from packages.database.db import SessionFactory
from packages.database.model_trace_models import ModelRuntimeTrace
from packages.model_runtime.registry import ModelAttemptEvent, register_model_telemetry_sink
from packages.model_runtime.trace_context import (
    ProviderWireEvent,
    register_provider_wire_telemetry_sink,
)

_INSTALLED = False
_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "password",
    "passwd",
    "secret",
    "cookie",
    "credential",
    "private_key",
    "session_token",
)
_HIDDEN_REASONING_KEYS = frozenset(
    {
        "reasoning",
        "reasoning_details",
        "reasoning_content",
        "thinking",
        "thinking_content",
        "chain_of_thought",
        "chain-of-thought",
    }
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/=]{8,}")
_SK_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _secret_key(key: str) -> bool:
    lowered = str(key or "").lower()
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _hidden_reasoning_key(key: str) -> bool:
    return str(key or "").strip().lower() in _HIDDEN_REASONING_KEYS


def _redact_string(value: str) -> str:
    text = _BEARER_RE.sub("Bearer [REDACTED]", value)
    return _SK_RE.sub("[REDACTED_API_KEY]", text)


def redact_trace_value(value: Any, *, key: str = "") -> Any:
    if key and _hidden_reasoning_key(key):
        return "[REDACTED_HIDDEN_REASONING]"
    if key and _secret_key(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): redact_trace_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_trace_value(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_string(str(value))


def encode_trace_envelope(payload: dict[str, Any]) -> str:
    """Encode the complete redacted model-visible packet plus exact-packet digest."""
    envelope = {
        "traceVersion": 2,
        "exactPayloadDigest": _digest(payload),
        "redactionApplied": True,
        "hiddenReasoningRedacted": True,
        "payload": redact_trace_value(payload),
    }
    return _canonical(envelope)


def _trace_row(
    *,
    metadata: dict[str, Any],
    run_id: str,
    conversation_id: str,
    attempt_id: str,
    phase: str,
    resource_id: str,
    provider: str,
    provider_model_id: str,
    payload: dict[str, Any],
    attempt: int = 1,
    latency_ms: int | None = None,
    classification: str | None = None,
    retryable: bool | None = None,
) -> ModelRuntimeTrace:
    return ModelRuntimeTrace(
        run_id=run_id[:64],
        conversation_id=conversation_id[:255],
        tenant_id=(str(metadata.get("tenant_id") or "").strip() or None),
        user_id=(str(metadata.get("user_id") or "").strip() or None),
        principal_id=(str(metadata.get("principal_id") or "").strip() or None),
        channel=(str(metadata.get("channel") or "").strip() or None),
        surface=(str(metadata.get("surface") or "").strip() or None),
        component=(str(metadata.get("runtime_component") or "").strip() or None),
        step=(int(metadata["runtime_step"]) if metadata.get("runtime_step") is not None else None),
        attempt_id=attempt_id[:36],
        phase=phase[:20],
        resource_id=resource_id[:255],
        provider=provider[:80],
        provider_model_id=provider_model_id[:255],
        attempt=max(1, int(attempt or 1)),
        latency_ms=(int(latency_ms) if latency_ms is not None else None),
        classification=(classification[:80] if classification else None),
        retryable=retryable,
        payload_json=encode_trace_envelope(payload),
        created_at=datetime.utcnow(),
    )


async def persist_model_attempt(event: ModelAttemptEvent) -> None:
    metadata = dict(getattr(event, "metadata", None) or {})
    conversation_id = str(metadata.get("conversation_id") or "").strip()
    run_id = str(metadata.get("runtime_run_id") or "").strip()
    if not conversation_id or not run_id:
        return

    attempt_id = str(getattr(event, "attempt_id", None) or "").strip()
    if not attempt_id:
        return

    payload = {
        "phase": event.phase,
        "attemptId": attempt_id,
        "resourceId": event.resource_id,
        "provider": event.provider,
        "providerModelId": event.provider_model_id,
        "attempt": event.attempt,
        "latencyMs": event.latency_ms,
        "classification": event.classification,
        "retryable": event.retryable,
        "detail": event.detail,
        "metadata": metadata,
        "input": getattr(event, "input_payload", None),
        "output": getattr(event, "output_payload", None),
    }
    row = _trace_row(
        metadata=metadata,
        run_id=run_id,
        conversation_id=conversation_id,
        attempt_id=attempt_id,
        phase=str(event.phase),
        resource_id=str(event.resource_id),
        provider=str(event.provider),
        provider_model_id=str(event.provider_model_id),
        payload=payload,
        attempt=event.attempt,
        latency_ms=event.latency_ms,
        classification=event.classification,
        retryable=event.retryable,
    )
    async with SessionFactory() as db:
        db.add(row)
        await db.commit()


async def persist_provider_wire_event(event: ProviderWireEvent) -> None:
    metadata = dict(event.metadata or {})
    conversation_id = str(metadata.get("conversation_id") or "").strip()
    run_id = str(metadata.get("runtime_run_id") or "").strip()
    if not conversation_id or not run_id or not event.wire_call_id:
        return
    phase = "wire_request" if event.phase == "request" else "wire_response"
    payload = {
        "phase": phase,
        "wireCallId": event.wire_call_id,
        "provider": event.provider,
        "providerModelId": event.provider_model_id,
        "status": event.status,
        "responseMetadata": event.response_metadata,
        "metadata": metadata,
        "wire": event.payload,
    }
    row = _trace_row(
        metadata=metadata,
        run_id=run_id,
        conversation_id=conversation_id,
        attempt_id=event.wire_call_id,
        phase=phase,
        resource_id=f"wire:{event.provider}:{event.provider_model_id}",
        provider=str(event.provider),
        provider_model_id=str(event.provider_model_id),
        payload=payload,
    )
    async with SessionFactory() as db:
        db.add(row)
        await db.commit()


def ensure_model_trace_sink() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    register_model_telemetry_sink(persist_model_attempt)
    register_provider_wire_telemetry_sink(persist_provider_wire_event)
    _INSTALLED = True


def _trace_json(row: ModelRuntimeTrace) -> dict[str, Any]:
    try:
        trace = json.loads(row.payload_json or "{}")
    except Exception:
        trace = {
            "traceVersion": 2,
            "redactionApplied": True,
            "hiddenReasoningRedacted": True,
            "payload": {},
        }
    return {
        "id": row.id,
        "runId": row.run_id,
        "attemptId": row.attempt_id,
        "step": row.step,
        "component": row.component,
        "phase": row.phase,
        "resourceId": row.resource_id,
        "provider": row.provider,
        "providerModelId": row.provider_model_id,
        "attempt": row.attempt,
        "latencyMs": row.latency_ms,
        "classification": row.classification,
        "retryable": row.retryable,
        "trace": trace,
        "createdAt": row.created_at.isoformat(),
    }


async def conversation_trace_report(
    db,
    *,
    conversation_id: str,
    user_id: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    query = select(ModelRuntimeTrace).where(
        ModelRuntimeTrace.conversation_id == conversation_id,
        ModelRuntimeTrace.user_id == user_id,
    )
    if tenant_id is not None:
        query = query.where(ModelRuntimeTrace.tenant_id == tenant_id)
    rows = list(
        (
            await db.scalars(
                query.order_by(ModelRuntimeTrace.created_at.asc()).limit(5000)
            )
        ).all()
    )

    grouped: OrderedDict[str, list[ModelRuntimeTrace]] = OrderedDict()
    for row in rows:
        grouped.setdefault(row.run_id, []).append(row)

    runs = []
    for run_id, run_rows in grouped.items():
        errors = [row for row in run_rows if row.phase == "error"]
        successes = [row for row in run_rows if row.phase == "success"]
        models = []
        seen = set()
        for row in run_rows:
            key = (row.provider, row.provider_model_id)
            if key not in seen:
                seen.add(key)
                models.append({"provider": row.provider, "model": row.provider_model_id})
        runs.append(
            {
                "runId": run_id,
                "startedAt": run_rows[0].created_at.isoformat(),
                "finishedAt": run_rows[-1].created_at.isoformat(),
                "modelCandidatesObserved": models,
                "errorCount": len(errors),
                "successCount": len(successes),
                "entries": [_trace_json(row) for row in run_rows],
            }
        )

    return {
        "conversationId": conversation_id,
        "traceVersion": 2,
        "redactionApplied": True,
        "hiddenReasoningRedacted": True,
        "entryCount": len(rows),
        "runCount": len(runs),
        "runs": runs,
    }
