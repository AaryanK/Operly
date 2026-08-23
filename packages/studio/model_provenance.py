"""Run-scoped durable model-attempt provenance for Studio."""
from __future__ import annotations

import contextvars
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select

from packages.database.db import SessionFactory
from packages.database.scope_models import StudioModelAttempt
from packages.model_runtime import register_model_telemetry_sink
from packages.model_runtime.registry import ModelAttemptEvent


@dataclass
class ProvenanceScope:
    run_id: str
    tenant_id: str
    model_turn: int = 0
    attempt_in_turn: int = 0
    rows: dict[tuple[int, str, int], str] = field(default_factory=dict)


_SCOPE: contextvars.ContextVar[ProvenanceScope | None] = contextvars.ContextVar(
    "operly_studio_model_provenance",
    default=None,
)
_INSTALLED = False


def begin(run_id: str, tenant_id: str):
    return _SCOPE.set(ProvenanceScope(run_id=run_id, tenant_id=tenant_id))


def end(token) -> None:
    _SCOPE.reset(token)


class TurnTrackingClient:
    def __init__(self, inner):
        self.inner = inner

    async def chat(self, messages, tools=None):
        scope = _SCOPE.get()
        if scope is not None:
            scope.model_turn += 1
            scope.attempt_in_turn = 0
        return await self.inner.chat(messages, tools)


def wrap_client_factory(factory):
    def wrapped(*args, **kwargs):
        return TurnTrackingClient(factory(*args, **kwargs))

    return wrapped


def _usage_payload(value: Any) -> str:
    if value is None:
        return "{}"
    if isinstance(value, dict):
        data = value
    else:
        data = {
            key: getattr(value, key)
            for key in ("input_tokens", "output_tokens", "total_tokens")
            if getattr(value, key, None) is not None
        }
    return json.dumps(data, sort_keys=True, default=str)[:8000]


async def telemetry_sink(event: ModelAttemptEvent) -> None:
    scope = _SCOPE.get()
    if scope is None:
        return

    key = (max(1, scope.model_turn), event.resource_id, int(event.attempt))
    if event.phase == "start":
        scope.attempt_in_turn += 1
        row = StudioModelAttempt(
            tenant_id=scope.tenant_id,
            run_id=scope.run_id,
            model_turn_index=max(1, scope.model_turn),
            provider_attempt_index=scope.attempt_in_turn,
            provider=event.provider,
            model_resource_id=event.resource_id,
            provider_model_id=event.provider_model_id,
            outcome="started",
            usage_json="{}",
            started_at=datetime.utcnow(),
        )
        async with SessionFactory() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
        scope.rows[key] = row.id
        return

    row_id = scope.rows.get(key)
    async with SessionFactory() as db:
        row = await db.get(StudioModelAttempt, row_id) if row_id else None
        if row is None:
            # Telemetry must never crash inference. A late terminal event is still
            # durable, but receives the next monotonic provider-attempt index.
            scope.attempt_in_turn += 1
            row = StudioModelAttempt(
                tenant_id=scope.tenant_id,
                run_id=scope.run_id,
                model_turn_index=max(1, scope.model_turn),
                provider_attempt_index=scope.attempt_in_turn,
                provider=event.provider,
                model_resource_id=event.resource_id,
                provider_model_id=event.provider_model_id,
                outcome=event.phase,
                started_at=datetime.utcnow(),
            )
            db.add(row)
        row.provider_model_id = event.provider_model_id or row.provider_model_id
        row.outcome = "succeeded" if event.phase == "success" else "failed"
        row.error_classification = event.classification
        row.failover_reason = (
            str(event.detail or "")[:300]
            or ("retryable provider failure" if event.retryable else None)
        )
        row.latency_ms = event.latency_ms
        row.usage_json = _usage_payload(getattr(event, "usage", None))
        row.completed_at = datetime.utcnow()
        await db.commit()


def install_telemetry() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    register_model_telemetry_sink(telemetry_sink)
    _INSTALLED = True


async def summary(db, run_id: str, tenant_id: str) -> dict[str, Any]:
    rows = list(
        (
            await db.scalars(
                select(StudioModelAttempt)
                .where(
                    StudioModelAttempt.run_id == run_id,
                    StudioModelAttempt.tenant_id == tenant_id,
                )
                .order_by(
                    StudioModelAttempt.model_turn_index,
                    StudioModelAttempt.provider_attempt_index,
                )
            )
        ).all()
    )
    models: list[str] = []
    for row in rows:
        label = row.provider_model_id or row.model_resource_id
        if label and label not in models:
            models.append(label)
    winners = [row for row in rows if row.outcome == "succeeded"]
    winner = (
        winners[-1].provider_model_id or winners[-1].model_resource_id
        if winners
        else None
    )
    return {
        "modelsParticipated": models,
        "winningModel": winner,
        "attemptCount": len(rows),
        "modelAttempts": [
            {
                "turn": row.model_turn_index,
                "attempt": row.provider_attempt_index,
                "provider": row.provider,
                "resourceId": row.model_resource_id,
                "modelId": row.provider_model_id,
                "outcome": row.outcome,
                "classification": row.error_classification,
                "failoverReason": row.failover_reason,
                "latencyMs": row.latency_ms,
                "usage": json.loads(row.usage_json or "{}"),
            }
            for row in rows
        ],
    }
