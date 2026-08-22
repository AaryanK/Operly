"""Provider-agnostic model resource catalog.

The harness reasons in capabilities, not provider/model names. Ox Alpha is the
default orchestrator today, while configured, runtime-registered, and provider-
discovered resources all share the same catalog contract.
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
        }


_LOCK = RLock()
_REGISTERED: dict[tuple[str, str], ModelResource] = {}
_DISCOVERED: dict[tuple[str, str], ModelResource] = {}


def _normalize(resource: ModelResource) -> ModelResource:
    provider = resource.provider.strip().lower()
    model_id = resource.id.strip()
    if not provider or not model_id:
        raise ValueError("Model provider and id are required")
    return ModelResource(
        id=model_id,
        provider=provider,
        capabilities=frozenset(
            str(item).strip().lower()
            for item in resource.capabilities
            if str(item).strip()
        ),
        free=bool(resource.free),
        priority=int(resource.priority),
        name=str(resource.name or "").strip(),
        context_length=(
            int(resource.context_length) if resource.context_length is not None else None
        ),
        input_modalities=frozenset(
            str(item).strip().lower()
            for item in resource.input_modalities
            if str(item).strip()
        ),
        output_modalities=frozenset(
            str(item).strip().lower()
            for item in resource.output_modalities
            if str(item).strip()
        ),
        supported_parameters=frozenset(
            str(item).strip()
            for item in resource.supported_parameters
            if str(item).strip()
        ),
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
        free=os.getenv("OPERLY_ORCHESTRATOR_FREE", "1").strip().lower()
        not in {"0", "false", "no"},
        priority=0,
        name="Ox Alpha" if route.primary == "stealth/ox-alpha" else route.primary,
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
                capabilities=frozenset(
                    str(cap).strip().lower()
                    for cap in capabilities
                    if str(cap).strip()
                ),
                free=bool(item.get("free", False)),
                priority=int(item.get("priority", 100)),
                name=str(item.get("name") or ""),
                context_length=(
                    int(item["context_length"])
                    if item.get("context_length") is not None
                    else None
                ),
                input_modalities=frozenset(
                    str(value).strip().lower()
                    for value in (item.get("input_modalities") or [])
                    if str(value).strip()
                ),
                output_modalities=frozenset(
                    str(value).strip().lower()
                    for value in (item.get("output_modalities") or [])
                    if str(value).strip()
                ),
                supported_parameters=frozenset(
                    str(value).strip()
                    for value in (item.get("supported_parameters") or [])
                    if str(value).strip()
                ),
            )
        )
    return resources


def model_resources() -> tuple[ModelResource, ...]:
    """Return orchestrator + configured/discovered/registered resources."""
    orchestrator = _orchestrator_resource()
    merged: dict[tuple[str, str], ModelResource] = {
        (orchestrator.provider, orchestrator.id): orchestrator,
    }
    with _LOCK:
        merged.update(_DISCOVERED)
    for resource in _configured_resources():
        merged[(resource.provider, resource.id)] = resource
    with _LOCK:
        merged.update(_REGISTERED)
    # The configured orchestrator remains authoritative even if provider discovery
    # returns the same id with different priority metadata.
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
