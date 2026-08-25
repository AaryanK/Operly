from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.model_trace_models import ModelRuntimeTrace
from packages.database.studio_source_models import StudioModelTrace


_USAGE_WINDOWS: dict[str, timedelta | None] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}


def normalize_usage_range(value: str | None) -> str:
    clean = str(value or "24h").strip().lower()
    return clean if clean in _USAGE_WINDOWS else "24h"


def _decoded_payload(payload_json: str | None) -> dict[str, Any]:
    try:
        value = json.loads(payload_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _usage_values(value: Any) -> tuple[int, int, int]:
    if not isinstance(value, dict):
        return (0, 0, 0)
    input_tokens = _as_int(value.get("input_tokens") or value.get("prompt_tokens"))
    output_tokens = _as_int(value.get("output_tokens") or value.get("completion_tokens"))
    total_tokens = _as_int(value.get("total_tokens") or value.get("total"))
    if not total_tokens and (input_tokens or output_tokens):
        total_tokens = input_tokens + output_tokens
    return input_tokens, output_tokens, total_tokens


def _bucket_key(value: datetime, range_name: str) -> str:
    if range_name == "1h":
        minute = (value.minute // 5) * 5
        return value.replace(minute=minute, second=0, microsecond=0).isoformat()
    if range_name == "24h":
        return value.replace(minute=0, second=0, microsecond=0).isoformat()
    return value.date().isoformat()


def _runtime_usage(row: ModelRuntimeTrace) -> tuple[int, int, int]:
    envelope = _decoded_payload(row.payload_json)
    payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
    output = payload.get("output") if isinstance(payload, dict) else {}
    usage = output.get("usage") if isinstance(output, dict) else None
    return _usage_values(usage)


def _studio_usage(row: StudioModelTrace) -> tuple[dict[str, Any], tuple[int, int, int]]:
    envelope = _decoded_payload(row.payload_json)
    payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
    return payload, _usage_values(payload.get("usage") if isinstance(payload, dict) else None)


async def build_admin_ai_usage(db: AsyncSession, range_name: str | None = None) -> dict[str, Any]:
    selected_range = normalize_usage_range(range_name)
    now = datetime.utcnow()
    window = _USAGE_WINDOWS[selected_range]
    cutoff = now - window if window is not None else None

    runtime_query = select(ModelRuntimeTrace).where(ModelRuntimeTrace.phase == "success")
    studio_query = select(StudioModelTrace).where(StudioModelTrace.phase == "response")
    if cutoff is not None:
        runtime_query = runtime_query.where(ModelRuntimeTrace.created_at >= cutoff)
        studio_query = studio_query.where(StudioModelTrace.created_at >= cutoff)

    runtime_rows = (
        await db.scalars(runtime_query.order_by(ModelRuntimeTrace.created_at.asc()))
    ).all()
    studio_rows = (
        await db.scalars(studio_query.order_by(StudioModelTrace.created_at.asc()))
    ).all()

    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0, "tracked_calls": 0}
    model_buckets: dict[tuple[str, str], dict[str, Any]] = {}
    time_buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0}
    )

    def add_usage(*, created_at: datetime, provider: str, model: str, usage: tuple[int, int, int]) -> None:
        input_tokens, output_tokens, total_tokens = usage
        provider_name = str(provider or "unknown").strip() or "unknown"
        model_name = str(model or "unknown").strip() or "unknown"

        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["total_tokens"] += total_tokens
        totals["calls"] += 1
        if total_tokens:
            totals["tracked_calls"] += 1

        key = (provider_name, model_name)
        bucket = model_buckets.setdefault(
            key,
            {
                "provider": provider_name,
                "model": model_name,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "calls": 0,
                "tracked_calls": 0,
            },
        )
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["total_tokens"] += total_tokens
        bucket["calls"] += 1
        if total_tokens:
            bucket["tracked_calls"] += 1

        time_bucket = time_buckets[_bucket_key(created_at, selected_range)]
        time_bucket["input_tokens"] += input_tokens
        time_bucket["output_tokens"] += output_tokens
        time_bucket["total_tokens"] += total_tokens
        time_bucket["calls"] += 1

    for row in runtime_rows:
        # Studio owns a dedicated trace stream. Skip any future runtime trace that
        # explicitly identifies itself as Studio to avoid double-counting.
        if str(row.surface or "").strip().lower() == "studio":
            continue
        add_usage(
            created_at=row.created_at,
            provider=row.provider,
            model=row.provider_model_id or row.resource_id,
            usage=_runtime_usage(row),
        )

    for row in studio_rows:
        payload, usage = _studio_usage(row)
        add_usage(
            created_at=row.created_at,
            provider=str(payload.get("provider") or "unknown"),
            model=str(payload.get("providerModelId") or payload.get("modelResourceId") or "unknown"),
            usage=usage,
        )

    by_model = sorted(
        model_buckets.values(),
        key=lambda item: (item["total_tokens"], item["calls"]),
        reverse=True,
    )
    total_tokens = totals["total_tokens"]
    for item in by_model:
        item["share_percent"] = round((item["total_tokens"] / total_tokens) * 100, 2) if total_tokens else 0.0

    series = [
        {"bucket": bucket, **values}
        for bucket, values in sorted(time_buckets.items(), key=lambda item: item[0])
    ]

    return {
        "range": selected_range,
        "generated_at": now.isoformat(),
        "totals": totals,
        "models": len(by_model),
        "by_model": by_model,
        "series": series,
        "coverage": {
            "tracked_calls": totals["tracked_calls"],
            "calls": totals["calls"],
            "percent": round((totals["tracked_calls"] / totals["calls"]) * 100, 1) if totals["calls"] else 0.0,
        },
    }
