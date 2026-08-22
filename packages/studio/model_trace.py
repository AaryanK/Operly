"""Owner-only Studio model request/response tracing.

Normal Studio activity remains sanitized and compact. This module captures the exact
provider-neutral packet passed from the context-bounded coding client into
``ModelChatAdapter`` for debugging, then persists a redacted copy plus a digest of
the exact unredacted packet. No chain-of-thought is synthesized or exposed: only
messages/tool schemas actually supplied by the harness and the model response/error.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from packages.database.db import SessionFactory
from packages.database.studio_source_models import StudioAgentRun, StudioModelTrace

_TRACE_RUN_ID: ContextVar[str | None] = ContextVar("operly_studio_model_trace_run_id", default=None)
_INSTALLED = False
_MAX_TRACE_JSON_CHARS = 1_500_000
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
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/=]{8,}")
_SK_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _secret_key(key: str) -> bool:
    lowered = str(key or "").lower()
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _redact_string(value: str) -> str:
    text = _BEARER_RE.sub("Bearer [REDACTED]", value)
    return _SK_RE.sub("[REDACTED_API_KEY]", text)


def redact_trace_value(value: Any, *, key: str = "") -> Any:
    """Return a JSON-safe trace copy with credential-shaped material removed."""
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
    if is_dataclass(value):
        return redact_trace_value(asdict(value))
    return _redact_string(str(value))


def _model_candidates(model: Any) -> list[dict[str, Any]]:
    candidates = getattr(model, "models", None) or (model,)
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        rows.append(
            {
                "resourceId": str(getattr(candidate, "id", "unknown")),
                "provider": str(getattr(candidate, "provider", "unknown")),
                "providerModelId": str(getattr(candidate, "provider_model_id", getattr(candidate, "id", "unknown"))),
                "tags": sorted(str(item) for item in getattr(candidate, "tags", ()) or ()),
                "capabilities": sorted(str(item) for item in getattr(candidate, "capabilities", ()) or ()),
            }
        )
    return rows


async def _persist_trace(phase: str, call_index: int, payload: dict[str, Any]) -> None:
    run_id = _TRACE_RUN_ID.get()
    if not run_id:
        return

    envelope = {
        "traceVersion": 1,
        "exactPayloadDigest": _digest(payload),
        "redactionApplied": True,
        "payload": redact_trace_value(payload),
    }
    encoded = _canonical(envelope)
    if len(encoded) > _MAX_TRACE_JSON_CHARS:
        # Preserve exact identity even when an unexpectedly giant packet cannot be
        # retained safely in one database row.
        envelope = {
            "traceVersion": 1,
            "exactPayloadDigest": _digest(payload),
            "redactionApplied": True,
            "truncated": True,
            "originalJsonChars": len(_canonical(payload)),
            "payload": {
                "notice": "Trace exceeded the durable debug-payload limit.",
                "summary": redact_trace_value(
                    {
                        "phase": phase,
                        "callIndex": call_index,
                        "messageCount": len(payload.get("messages") or []),
                        "toolCount": len(payload.get("tools") or []),
                    }
                ),
            },
        }
        encoded = _canonical(envelope)

    async with SessionFactory() as db:
        run = await db.get(StudioAgentRun, run_id)
        if run is None:
            return
        existing = await db.scalar(
            select(StudioModelTrace).where(
                StudioModelTrace.run_id == run_id,
                StudioModelTrace.call_index == int(call_index),
                StudioModelTrace.phase == phase,
            )
        )
        if existing is None:
            db.add(
                StudioModelTrace(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    call_index=int(call_index),
                    phase=phase,
                    payload_json=encoded,
                    created_at=datetime.utcnow(),
                )
            )
        else:
            existing.payload_json = encoded
        await db.commit()


class TracingModelChatClient:
    """Trace wrapper placed immediately in front of ModelChatAdapter.

    Because the context-window wrapper sits outside this object, ``messages`` here
    are exactly the compacted messages the ModelChatAdapter will convert to an
    InferenceRequest. The wrapper is provider-neutral and never sees credentials.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self._call_index = 0

    @property
    def model(self) -> Any:
        """Keep the debug wrapper transparent to existing model provenance code."""
        return getattr(self.inner, "model", None)

    @property
    def last_model(self) -> str:
        return str(getattr(self.inner, "last_model", getattr(getattr(self.inner, "model", None), "id", "unknown")))

    async def chat(self, messages: list[dict[str, Any]], tools=None) -> dict[str, Any]:
        self._call_index += 1
        call_index = self._call_index
        tool_list = list(tools or ())
        model = getattr(self.inner, "model", None)
        budget = getattr(self.inner, "budget", None)
        request_payload = {
            "callIndex": call_index,
            "modelResourceId": str(getattr(model, "id", "unknown")),
            "candidateModels": _model_candidates(model) if model is not None else [],
            "budget": asdict(budget) if budget is not None and is_dataclass(budget) else None,
            "messages": messages,
            "tools": tool_list,
            "messageCount": len(messages),
            "toolCount": len(tool_list),
            "messageJsonChars": len(_canonical(messages)),
            "toolJsonChars": len(_canonical(tool_list)),
        }
        await _persist_trace("request", call_index, request_payload)
        try:
            message = await self.inner.chat(messages, tool_list)
        except BaseException as error:
            await _persist_trace(
                "error",
                call_index,
                {
                    "callIndex": call_index,
                    "type": type(error).__name__,
                    "message": str(error),
                    "classification": getattr(error, "classification", None),
                    "retryable": getattr(error, "retryable", None),
                    "provider": getattr(error, "provider", None),
                    "modelId": getattr(error, "model_id", None),
                },
            )
            raise

        result = getattr(self.inner, "last_result", None)
        usage = getattr(result, "usage", None)
        response_payload = {
            "callIndex": call_index,
            "message": message,
            "modelResourceId": getattr(result, "model_resource_id", None),
            "provider": getattr(result, "provider", None),
            "providerModelId": getattr(result, "provider_model_id", None),
            "latencyMs": getattr(result, "latency_ms", None),
            "finishReason": getattr(result, "finish_reason", None),
            "attempt": getattr(result, "attempt", None),
            "usage": asdict(usage) if usage is not None and is_dataclass(usage) else None,
        }
        await _persist_trace("response", call_index, response_payload)
        return message


def install_agent_run_trace_context() -> None:
    """Bind Studio run identity to the async task without changing agent APIs."""
    global _INSTALLED
    if _INSTALLED:
        return
    from packages.studio import agent_runs

    original = agent_runs._execute_run
    if getattr(original, "_operly_model_trace_wrapped", False):
        _INSTALLED = True
        return

    async def traced_execute_run(run_id: str) -> None:
        token = _TRACE_RUN_ID.set(run_id)
        try:
            await original(run_id)
        finally:
            _TRACE_RUN_ID.reset(token)

    traced_execute_run._operly_model_trace_wrapped = True  # type: ignore[attr-defined]
    agent_runs._execute_run = traced_execute_run
    _INSTALLED = True


async def trace_rows(db, tenant_id: str, run_id: str) -> list[StudioModelTrace]:
    return list(
        (
            await db.scalars(
                select(StudioModelTrace)
                .where(StudioModelTrace.tenant_id == tenant_id, StudioModelTrace.run_id == run_id)
                .order_by(StudioModelTrace.call_index.asc(), StudioModelTrace.created_at.asc())
                .limit(500)
            )
        ).all()
    )


def trace_json(row: StudioModelTrace) -> dict[str, Any]:
    try:
        payload = json.loads(row.payload_json or "{}")
    except Exception:
        payload = {"traceVersion": 1, "redactionApplied": True, "payload": {}}
    return {
        "id": row.id,
        "callIndex": row.call_index,
        "phase": row.phase,
        "trace": payload,
        "createdAt": row.created_at.isoformat(),
    }
