"""Platform-admin FLOW browser over Operly's durable runtime trace evidence.

FLOW is intentionally read-only. It exposes the same redacted, hidden-reasoning-safe
packets already persisted by the canonical runtime tracing sink and never replays or
mutates production work.
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.admin_router import require_platform_admin
from apps.api.dependencies import AccountAuthContext, get_db
from packages.database.model_trace import _trace_json
from packages.database.model_trace_models import ModelRuntimeTrace

router = APIRouter(prefix="/api/flow", tags=["flow"])


def _flow_enabled() -> bool:
    configured = os.getenv("OPERLY_FLOW_ENABLED")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    environment = os.getenv("OPERLY_ENV", os.getenv("APP_ENV", "development")).strip().lower()
    return environment not in {"production", "prod"}


async def require_flow_admin(
    account: AccountAuthContext = Depends(require_platform_admin),
) -> AccountAuthContext:
    """Keep FLOW convenient locally while requiring explicit production enablement."""
    if not _flow_enabled():
        raise HTTPException(status_code=404, detail="FLOW is disabled in this environment")
    return account


def _decoded_payload(payload_json: str | None) -> dict[str, Any]:
    try:
        value = json.loads(payload_json or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _application_payload(row: ModelRuntimeTrace) -> dict[str, Any]:
    envelope = _decoded_payload(row.payload_json)
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("event")
    if isinstance(nested, dict):
        return nested
    nested = payload.get("payload")
    return nested if isinstance(nested, dict) else payload


def _usage_values(value: Any) -> tuple[int, int, int]:
    if not isinstance(value, dict):
        return (0, 0, 0)
    input_tokens = int(value.get("input_tokens") or value.get("prompt_tokens") or 0)
    output_tokens = int(value.get("output_tokens") or value.get("completion_tokens") or 0)
    total_tokens = int(value.get("total_tokens") or input_tokens + output_tokens or 0)
    return input_tokens, output_tokens, total_tokens


def _run_status(rows: list[ModelRuntimeTrace]) -> str:
    errors = [row for row in rows if row.phase == "error"]
    successes = [row for row in rows if row.phase == "success"]
    blocked = [
        row
        for row in rows
        if row.phase == "capability.rejected"
        and str(_application_payload(row).get("state") or "").lower() == "blocked"
    ]
    completed = [
        row
        for row in rows
        if row.phase == "workflow.completed"
        and str(_application_payload(row).get("state") or "").lower() == "completed"
    ]
    if blocked:
        return "blocked"
    if errors and not successes and not completed:
        return "failed"
    if errors and (successes or completed):
        return "recovered"
    if successes or completed:
        return "success"
    return "running"


def _run_summary(run_id: str, rows: list[ModelRuntimeTrace]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: row.created_at)
    first, last = ordered[0], ordered[-1]

    models: list[dict[str, str]] = []
    seen_models: set[tuple[str, str]] = set()
    components: list[str] = []
    seen_components: set[str] = set()
    phases: list[str] = []
    seen_phases: set[str] = set()
    input_tokens = output_tokens = total_tokens = 0

    for row in ordered:
        model_key = (row.provider, row.provider_model_id)
        if model_key not in seen_models and model_key not in {
            ("operly", "runtime"),
            ("operly", "trusted-runtime"),
        }:
            seen_models.add(model_key)
            models.append({"provider": row.provider, "model": row.provider_model_id})
        if row.component and row.component not in seen_components:
            seen_components.add(row.component)
            components.append(row.component)
        if row.phase and row.phase not in seen_phases:
            seen_phases.add(row.phase)
            phases.append(row.phase)

        if row.phase == "success":
            envelope = _decoded_payload(row.payload_json)
            payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
            output = payload.get("output") if isinstance(payload, dict) else {}
            usage = output.get("usage") if isinstance(output, dict) else None
            in_count, out_count, total_count = _usage_values(usage)
            input_tokens += in_count
            output_tokens += out_count
            total_tokens += total_count

    elapsed_ms = max(0, int((last.created_at - first.created_at).total_seconds() * 1000))
    return {
        "runId": run_id,
        "conversationId": first.conversation_id,
        "tenantId": first.tenant_id,
        "userId": first.user_id,
        "principalId": first.principal_id,
        "surface": first.surface or first.channel or "runtime",
        "channel": first.channel,
        "components": components,
        "phases": phases,
        "status": _run_status(ordered),
        "startedAt": first.created_at.isoformat(),
        "finishedAt": last.created_at.isoformat(),
        "durationMs": elapsed_ms,
        "entryCount": len(ordered),
        "errorCount": sum(1 for row in ordered if row.phase == "error"),
        "modelCandidatesObserved": models,
        "tokenUsage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": total_tokens,
        },
    }


async def _recent_run_rows(
    db: AsyncSession,
    *,
    limit: int,
    surface: str | None,
) -> list[ModelRuntimeTrace]:
    filters = []
    if surface:
        filters.append(ModelRuntimeTrace.surface == surface)

    run_ids = list(
        (
            await db.scalars(
                select(ModelRuntimeTrace.run_id)
                .where(*filters)
                .group_by(ModelRuntimeTrace.run_id)
                .order_by(desc(func.max(ModelRuntimeTrace.created_at)))
                .limit(limit)
            )
        ).all()
    )
    if not run_ids:
        return []
    return list(
        (
            await db.scalars(
                select(ModelRuntimeTrace)
                .where(ModelRuntimeTrace.run_id.in_(run_ids))
                .order_by(ModelRuntimeTrace.created_at.asc())
            )
        ).all()
    )


@router.get("/runs")
async def list_flow_runs(
    limit: int = Query(default=100, ge=1, le=250),
    surface: str | None = Query(default=None, max_length=80),
    _: AccountAuthContext = Depends(require_flow_admin),
    db: AsyncSession = Depends(get_db),
):
    """List the most recent durable runtime executions across the Operly platform."""
    rows = await _recent_run_rows(db, limit=limit, surface=surface)
    grouped: OrderedDict[str, list[ModelRuntimeTrace]] = OrderedDict()
    for row in rows:
        grouped.setdefault(row.run_id, []).append(row)
    runs = [_run_summary(run_id, run_rows) for run_id, run_rows in grouped.items()]
    runs.sort(key=lambda item: str(item.get("startedAt") or ""), reverse=True)
    return {
        "mode": "FLOW",
        "live": True,
        "readOnly": True,
        "redactionApplied": True,
        "hiddenReasoningRedacted": True,
        "coverage": "canonical-agent-runtime",
        "runCount": len(runs),
        "runs": runs[:limit],
    }


@router.get("/runs/{run_id}")
async def get_flow_run(
    run_id: str,
    _: AccountAuthContext = Depends(require_flow_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return the complete redacted event path for one canonical runtime execution."""
    rows = list(
        (
            await db.scalars(
                select(ModelRuntimeTrace)
                .where(ModelRuntimeTrace.run_id == run_id)
                .order_by(ModelRuntimeTrace.created_at.asc())
                .limit(10000)
            )
        ).all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="FLOW run not found")
    return {
        **_run_summary(run_id, rows),
        "mode": "FLOW",
        "readOnly": True,
        "redactionApplied": True,
        "hiddenReasoningRedacted": True,
        "coverage": "canonical-agent-runtime",
        "entries": [_trace_json(row) for row in rows],
    }
