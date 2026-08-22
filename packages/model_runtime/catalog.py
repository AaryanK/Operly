"""Provider-agnostic model resource catalog.

Models are data resources with factual capabilities plus operator-facing tags and
selection traits. Provider discovery/configuration may replace the concrete model
without changing any harness.
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
    name: str = ""
    context_length: int | None = None
    input_modalities: frozenset[str] = frozenset()
    output_modalities: frozenset[str] = frozenset()
    supported_parameters: frozenset[str] = frozenset()
    tags: frozenset[str] = frozenset()
    latency_class: str | None = None
    cost_class: str | None = None
    quality_class: str | None = None
    locality: str | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "name": self.name or self.id,
            "capabilities": sorted(self.capabilities),
            "free": self.free,
            "priority": self.priority,
            "context_length": self.context_length,
            "input_modalities": sorted(self.input_modalities),
            "output_modalities": sorted(self.output_modalities),
            "supported_parameters": sorted(self.supported_parameters),
            "tags": sorted(self.tags),
            "latency_class": self.latency_class,
            "cost_class": self.cost_class,
            "quality_class": self.quality_class,
            "locality": self.locality,
        }


_LOCK = RLock()
_REGISTERED: dict[tuple[str, str], ModelResource] = {}
_DISCOVERED: dict[tuple[str, str], ModelResource] = {}


def _norm_set(values) -> frozenset[str]:
    return frozenset(
        str(item).strip().lower()
        for item in values
        if str(item).strip()
    )


def _normalize(resource: ModelResource) -> ModelResource:
    provider = resource.provider.strip().lower()
    model_id = resource.id.strip()
    if not provider or not model_id:
        raise ValueError("Model provider and id are required")
    tags = set(_norm_set(resource.tags))
    if resource.free:
        tags.add("free")
    if resource.locality:
        tags.add(str(resource.locality).strip().lower())
    return ModelResource(
        id=model_id,
        provider=provider,
        capabilities=_norm_set(resource.capabilities),
        free=bool(resource.free),
        priority=int(resource.priority),
        name=str(resource.name or "").strip(),
        context_length=(
            int(resource.context_length) if resource.context_length is not None else None
        ),
        input_modalities=_norm_set(resource.input_modalities),
        output_modalities=_norm_set(resource.output_modalities),
        supported_parameters=frozenset(
            str(item).strip()
            for item in resource.supported_parameters
            if str(item).strip()
        ),
        tags=frozenset(tags),
        latency_class=str(resource.latency_class).strip().lower() if resource.latency_class else None,
        cost_class=str(resource.cost_class).strip().lower() if resource.cost_class else ("free" if resource.free else None),
        quality_class=str(resource.quality_class).strip().lower() if resource.quality_class else None,
        locality=str(resource.locality).strip().lower() if resource.locality else None,
    )


def register_model_resource(resource: ModelResource, *, replace: bool = False) -> None:
    normalized = _normalize(resource)
    key = (normalized.provider, normalized.id)
    with _LOCK:
        if key in _REGISTERED and not replace:
            raise ValueError(f"Model resource already registered: {key[0]}/{key[1]}")
        _REGISTERED[key] = normalized


def replace_discovered_resources(provider: str, resources: list[ModelResource]) -> None:
    """Atomically replace one provider's discovered catalog snapshot."""
    provider_key = str(provider or "").strip().lower()
    if not provider_key:
        raise ValueError("Provider is required")
    normalized = [_normalize(resource) for resource in resources]
    if any(resource.provider != provider_key for resource in normalized):
        raise ValueError("Discovered model provider does not match discovery source")
    with _LOCK:
        for key in [key for key in _DISCOVERED if key[0] == provider_key]:
            del _DISCOVERED[key]
        for resource in normalized:
            _DISCOVERED[(resource.provider, resource.id)] = resource


def _orchestrator_resource() -> ModelResource:
    route = model_route("business_agent")
    capabilities = _norm_set(
        os.getenv(
            "OPERLY_ORCHESTRATOR_CAPABILITIES",
            "text,reasoning,coding,vision,video,tools",
        ).split(",")
    )
    tags = set(
        _norm_set(
            os.getenv(
                "OPERLY_ORCHESTRATOR_TAGS",
                "default,fast,free,tools",
            ).split(",")
        )
    )
    free = os.getenv("OPERLY_ORCHESTRATOR_FREE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    if free:
        tags.add("free")
    return ModelResource(
        id=route.primary,
        provider=route.provider,
        capabilities=capabilities,
        free=free,
        priority=0,
        name="Ox Alpha" if route.primary == "stealth/ox-alpha" else route.primary,
        tags=frozenset(tags),
        latency_class=os.getenv("OPERLY_ORCHESTRATOR_LATENCY_CLASS", "fast").strip().lower() or None,
        cost_class="free" if free else None,
        quality_class=os.getenv("OPERLY_ORCHESTRATOR_QUALITY_CLASS", "").strip().lower() or None,
        locality=os.getenv("OPERLY_ORCHESTRATOR_LOCALITY", "remote").strip().lower() or None,
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
                capabilities=_norm_set(capabilities),
                free=bool(item.get("free", False)),
                priority=int(item.get("priority", 100)),
                name=str(item.get("name") or ""),
                context_length=(
                    int(item["context_length"])
                    if item.get("context_length") is not None
                    else None
                ),
                input_modalities=_norm_set(item.get("input_modalities") or []),
                output_modalities=_norm_set(item.get("output_modalities") or []),
                supported_parameters=frozenset(
                    str(value).strip()
                    for value in (item.get("supported_parameters") or [])
                    if str(value).strip()
                ),
                tags=_norm_set(item.get("tags") or []),
                latency_class=str(item.get("latency_class") or "").strip().lower() or None,
                cost_class=str(item.get("cost_class") or "").strip().lower() or None,
                quality_class=str(item.get("quality_class") or "").strip().lower() or None,
                locality=str(item.get("locality") or "").strip().lower() or None,
            )
        )
    return resources


def model_resources() -> tuple[ModelResource, ...]:
    """Return orchestrator + configured/discovered/registered resources."""
    orchestrator = _normalize(_orchestrator_resource())
    merged: dict[tuple[str, str], ModelResource] = {
        (orchestrator.provider, orchestrator.id): orchestrator,
    }
    with _LOCK:
        merged.update(_DISCOVERED)
    for resource in _configured_resources():
        normalized = _normalize(resource)
        merged[(normalized.provider, normalized.id)] = normalized
    with _LOCK:
        merged.update(_REGISTERED)
    merged[(orchestrator.provider, orchestrator.id)] = orchestrator
    return tuple(
        sorted(
            merged.values(),
            key=lambda item: (item.priority, not item.free, item.provider, item.id),
        )
    )


def select_model_resource(
    capability: str,
    *,
    exclude: tuple[str, str] | None = None,
    prefer_free: bool = True,
    prefer_tags: frozenset[str] = frozenset(),
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
            -len(set(prefer_tags) & set(item.tags)),
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
