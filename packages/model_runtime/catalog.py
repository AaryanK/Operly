"""Provider-agnostic model resource catalog.

The harness reasons in capabilities, not provider/model names. The default
orchestrator is Ox Alpha, while additional model resources can be registered at
runtime or supplied through OPERLY_MODEL_CATALOG_JSON without changing harness
code.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from threading import RLock

from packages.model_runtime.portfolio import model_route


@dataclass(frozen=True, slots=True)
class ModelResource:
    id: str
    provider: str
    capabilities: frozenset[str]
    free: bool = False
    priority: int = 100


_LOCK = RLock()
_REGISTERED: dict[tuple[str, str], ModelResource] = {}


def register_model_resource(resource: ModelResource, *, replace: bool = False) -> None:
    key = (resource.provider.strip().lower(), resource.id.strip())
    if not all(key):
        raise ValueError("Model provider and id are required")
    normalized = ModelResource(
        id=key[1],
        provider=key[0],
        capabilities=frozenset(str(item).strip().lower() for item in resource.capabilities if str(item).strip()),
        free=bool(resource.free),
        priority=int(resource.priority),
    )
    with _LOCK:
        if key in _REGISTERED and not replace:
            raise ValueError(f"Model resource already registered: {key[0]}/{key[1]}")
        _REGISTERED[key] = normalized


def _orchestrator_resource() -> ModelResource:
    route = model_route("business_agent")
    capabilities = frozenset(
        item.strip().lower()
        for item in os.getenv(
            "OPERLY_ORCHESTRATOR_CAPABILITIES",
            "text,reasoning,coding,vision,video,tools",
        ).split(",")
        if item.strip()
    )
    return ModelResource(
        id=route.primary,
        provider=route.provider,
        capabilities=capabilities,
        free=os.getenv("OPERLY_ORCHESTRATOR_FREE", "1").strip().lower() not in {"0", "false", "no"},
        priority=0,
    )


def _configured_resources() -> list[ModelResource]:
    raw = os.getenv("OPERLY_MODEL_CATALOG_JSON", "").strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OPERLY_MODEL_CATALOG_JSON must contain valid JSON") from exc
    if not isinstance(value, list):
        raise RuntimeError("OPERLY_MODEL_CATALOG_JSON must be a JSON array")

    resources: list[ModelResource] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        provider = str(item.get("provider") or "").strip().lower()
        capabilities = item.get("capabilities") or []
        if not model_id or not provider or not isinstance(capabilities, list):
            continue
        resources.append(
            ModelResource(
                id=model_id,
                provider=provider,
                capabilities=frozenset(str(cap).strip().lower() for cap in capabilities if str(cap).strip()),
                free=bool(item.get("free", False)),
                priority=int(item.get("priority", 100)),
            )
        )
    return resources


def model_resources() -> tuple[ModelResource, ...]:
    """Return orchestrator + configured/registered resources deterministically."""
    orchestrator = _orchestrator_resource()
    merged: dict[tuple[str, str], ModelResource] = {
        (orchestrator.provider, orchestrator.id): orchestrator,
    }
    for resource in _configured_resources():
        merged[(resource.provider, resource.id)] = resource
    with _LOCK:
        merged.update(_REGISTERED)
    return tuple(sorted(merged.values(), key=lambda item: (item.priority, not item.free, item.provider, item.id)))


def select_model_resource(
    capability: str,
    *,
    exclude: tuple[str, str] | None = None,
    prefer_free: bool = True,
) -> ModelResource | None:
    wanted = str(capability or "").strip().lower()
    if not wanted:
        return None
    candidates = [
        resource
        for resource in model_resources()
        if wanted in resource.capabilities
        and (exclude is None or (resource.provider, resource.id) != exclude)
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            0 if (prefer_free and item.free) else 1,
            item.priority,
            item.provider,
            item.id,
        )
    )
    return candidates[0]


def has_delegate_models() -> bool:
    orchestrator = _orchestrator_resource()
    return any(
        (resource.provider, resource.id) != (orchestrator.provider, orchestrator.id)
        for resource in model_resources()
    )
