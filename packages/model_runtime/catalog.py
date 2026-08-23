"""Provider-agnostic model resource catalog.

Models are data resources with factual capabilities plus operator-facing tags,
routing traits, provider redundancy metadata, and usage-cost metadata. Provider
discovery/configuration may replace concrete routes without changing any harness.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from threading import RLock

from packages.model_runtime.portfolio import model_route


_PROVIDER_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "openrouter": (
        "OPEN_ROUTER_API",
        "OPENROUTER_API_KEY",
        "OPEN_ROUTER_API_KEY",
        "openrouter_api_key",
    ),
    "ollama": ("OLLAMA_API_KEY", "ollama_api_key"),
    "groq": ("GROQ_API_KEY", "groq_api_key"),
    "gemini": ("GEMINI_API_KEY", "gemini_api_key", "GOOGLE_API_KEY"),
    "nvidia": ("NVIDIA_API_KEY", "nvidia_api_key"),
}


def provider_is_configured(provider: str) -> bool:
    names = _PROVIDER_CREDENTIALS.get(str(provider or "").strip().lower(), ())
    return any(os.getenv(name, "").strip() for name in names)


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
    canonical_id: str = ""
    billing_mode: str | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    verified_latency_ms: int | None = None

    def usage_cost_label(self) -> str:
        if self.billing_mode == "free-route":
            return "$0 route"
        if self.billing_mode == "free-tier":
            return "Free tier / quota"
        if (
            self.input_cost_per_million is not None
            or self.output_cost_per_million is not None
        ):
            input_cost = (
                "?"
                if self.input_cost_per_million is None
                else f"${self.input_cost_per_million:g}/M input"
            )
            output_cost = (
                "?"
                if self.output_cost_per_million is None
                else f"${self.output_cost_per_million:g}/M output"
            )
            return f"{input_cost}; {output_cost}"
        return self.cost_class or "Unknown"

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "name": self.name or self.id,
            "canonical_id": self.canonical_id or self.id,
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
            "billing_mode": self.billing_mode,
            "input_cost_per_million": self.input_cost_per_million,
            "output_cost_per_million": self.output_cost_per_million,
            "verified_latency_ms": self.verified_latency_ms,
            "usage_cost": self.usage_cost_label(),
            "provider_configured": provider_is_configured(self.provider),
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


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    billing_mode = (
        str(resource.billing_mode).strip().lower()
        if resource.billing_mode
        else ("free-tier" if resource.free else None)
    )
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
        latency_class=(
            str(resource.latency_class).strip().lower()
            if resource.latency_class
            else None
        ),
        cost_class=(
            str(resource.cost_class).strip().lower()
            if resource.cost_class
            else ("free" if resource.free else None)
        ),
        quality_class=(
            str(resource.quality_class).strip().lower()
            if resource.quality_class
            else None
        ),
        locality=(
            str(resource.locality).strip().lower()
            if resource.locality
            else None
        ),
        canonical_id=str(resource.canonical_id or model_id).strip(),
        billing_mode=billing_mode,
        input_cost_per_million=_optional_float(resource.input_cost_per_million),
        output_cost_per_million=_optional_float(resource.output_cost_per_million),
        verified_latency_ms=(
            int(resource.verified_latency_ms)
            if resource.verified_latency_ms is not None
            else None
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


def _free_card(
    provider: str,
    model_id: str,
    *,
    name: str | None = None,
    canonical_id: str | None = None,
    capabilities: tuple[str, ...] = ("text", "reasoning"),
    tags: tuple[str, ...] = ("verified", "reliable"),
    latency_ms: int | None = None,
    priority: int = 100,
    quality_class: str | None = None,
    billing_mode: str = "free-tier",
) -> ModelResource:
    tag_set = set(tags)
    tag_set.update(capabilities)
    tag_set.add("free")
    if latency_ms is not None:
        if latency_ms <= 500:
            tag_set.add("fast")
            latency_class = "very-fast"
        elif latency_ms <= 2000:
            tag_set.add("fast")
            latency_class = "fast"
        elif latency_ms >= 10_000:
            tag_set.add("slow")
            latency_class = "slow"
        else:
            latency_class = "moderate"
    else:
        latency_class = "fast" if "fast" in tag_set else None
    zero_cost = billing_mode == "free-route"
    return ModelResource(
        id=model_id,
        provider=provider,
        name=name or model_id,
        canonical_id=canonical_id or model_id,
        capabilities=frozenset(capabilities),
        free=True,
        priority=priority,
        tags=frozenset(tag_set),
        latency_class=latency_class,
        cost_class="free",
        quality_class=quality_class,
        locality="remote",
        billing_mode=billing_mode,
        input_cost_per_million=0.0 if zero_cost else None,
        output_cost_per_million=0.0 if zero_cost else None,
        verified_latency_ms=latency_ms,
    )


def _verified_free_resources() -> list[ModelResource]:
    """Static cards for routes the operator verified against production keys."""
    rows: list[ModelResource] = []

    if provider_is_configured("groq"):
        rows.extend(
            [
                _free_card(
                    "groq",
                    "openai/gpt-oss-120b",
                    canonical_id="openai:gpt-oss-120b",
                    capabilities=("text", "reasoning", "coding", "tools"),
                    tags=("verified", "reliable", "orchestrator", "coding", "heavy"),
                    latency_ms=302,
                    priority=12,
                    quality_class="high",
                ),
                _free_card(
                    "groq",
                    "openai/gpt-oss-20b",
                    canonical_id="openai:gpt-oss-20b",
                    capabilities=("text", "reasoning", "coding", "tools"),
                    tags=("verified", "reliable", "orchestrator", "coding", "small"),
                    latency_ms=140,
                    priority=5,
                    quality_class="balanced",
                ),
                _free_card(
                    "groq",
                    "qwen/qwen3.6-27b",
                    canonical_id="qwen:qwen3.6-27b",
                    capabilities=("text", "reasoning", "coding"),
                    tags=("verified", "reliable", "coding", "small"),
                    latency_ms=232,
                    priority=8,
                    quality_class="balanced",
                ),
                _free_card(
                    "groq",
                    "groq/compound",
                    capabilities=("text", "reasoning", "research"),
                    tags=("verified", "reliable", "research", "fast"),
                    priority=35,
                    quality_class="high",
                ),
            ]
        )

    if provider_is_configured("gemini"):
        for index, model_id in enumerate(
            (
                "gemini-3-flash-preview",
                "gemini-3.1-flash-lite",
                "gemini-3.5-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.6-flash",
                "gemini-flash-lite-latest",
            )
        ):
            lite = "lite" in model_id
            rows.append(
                _free_card(
                    "gemini",
                    model_id,
                    canonical_id=f"google:{model_id}",
                    capabilities=("text", "reasoning", "coding", "tools", "vision"),
                    tags=(
                        "verified",
                        "reliable",
                        "orchestrator",
                        "coding",
                        "fast",
                        *(("small",) if lite else ()),
                    ),
                    priority=18 + index,
                    quality_class="balanced" if lite else "high",
                )
            )

    if provider_is_configured("nvidia"):
        nvidia_rows = (
            ("meta/llama-3.1-70b-instruct", ("text", "reasoning"), ("heavy",), None, 60),
            ("meta/llama-3.1-8b-instruct", ("text", "reasoning"), ("small", "fast"), None, 25),
            ("meta/llama-3.2-11b-vision-instruct", ("text", "reasoning", "vision"), ("small",), None, 45),
            ("meta/llama-3.2-90b-vision-instruct", ("text", "reasoning", "vision"), ("heavy",), None, 65),
            ("minimaxai/minimax-m3", ("text", "reasoning", "coding"), ("heavy", "coding"), None, 55),
            ("mistralai/mistral-nemotron", ("text", "reasoning", "coding"), ("coding",), None, 50),
            ("moonshotai/kimi-k3", ("text", "reasoning", "coding"), ("heavy", "coding"), None, 58),
            ("nvidia/nemotron-3-nano-30b-a3b", ("text", "reasoning", "coding"), ("small", "coding", "fast"), None, 28),
            ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", ("text", "reasoning", "coding"), ("small", "coding"), None, 32),
            ("nvidia/nemotron-3-super-120b-a12b", ("text", "reasoning", "coding"), ("heavy", "coding"), None, 42),
            ("nvidia/nemotron-3-ultra-550b-a55b", ("text", "reasoning", "coding"), ("heavy", "coding"), 536, 16),
            ("nvidia/nemotron-3.5-lightning-30b-a3b", ("text", "reasoning", "coding"), ("small", "coding", "fast"), None, 22),
            ("openai/gpt-oss-20b", ("text", "reasoning", "coding"), ("small", "coding", "fast"), None, 24),
            ("stepfun-ai/step-3.7-flash", ("text", "reasoning", "coding"), ("small", "coding", "fast"), None, 26),
        )
        for model_id, caps, tags, latency_ms, priority in nvidia_rows:
            canonical = (
                "openai:gpt-oss-20b"
                if model_id == "openai/gpt-oss-20b"
                else model_id.replace("nvidia/", "nvidia:")
            )
            rows.append(
                _free_card(
                    "nvidia",
                    model_id,
                    canonical_id=canonical,
                    capabilities=caps,
                    tags=("verified", "reliable", *tags),
                    latency_ms=latency_ms,
                    priority=priority,
                    quality_class="high" if "heavy" in tags else "balanced",
                )
            )

    if provider_is_configured("openrouter"):
        openrouter_rows = (
            ("cohere/north-mini-code:free", ("text", "reasoning", "coding"), ("coding", "small", "fast"), None, 34),
            ("dots-studio/dots-3-note-preview:free", ("text", "reasoning"), ("small", "fast"), None, 48),
            ("liquid/lfm-2.5-2.6b:free", ("text", "reasoning"), ("small", "fast"), None, 30),
            ("nvidia/nemotron-3-nano-30b-a3b:free", ("text", "reasoning", "coding"), ("coding", "small", "fast"), None, 31),
            ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", ("text", "reasoning", "coding"), ("coding", "small"), None, 33),
            ("nvidia/nemotron-3-super-120b-a12b:free", ("text", "reasoning", "coding"), ("coding", "heavy"), None, 40),
            ("nvidia/nemotron-3-ultra-550b-a55b:free", ("text", "reasoning", "coding"), ("coding", "heavy"), 1044, 19),
            ("nvidia/nemotron-3.5-lightning:free", ("text", "reasoning", "coding"), ("coding", "small", "fast"), None, 29),
            ("nvidia/nemotron-nano-12b-v2-vl:free", ("text", "reasoning", "vision"), ("small", "fast"), None, 36),
            ("nvidia/nemotron-nano-9b-v2:free", ("text", "reasoning"), ("small", "fast"), None, 35),
            ("poolside/laguna-s-2.1:free", ("text", "reasoning", "coding"), ("coding",), None, 37),
            ("poolside/laguna-xs-2.1:free", ("text", "reasoning", "coding"), ("coding", "small", "fast"), None, 32),
            ("stealth/ox-alpha", ("text", "reasoning", "coding", "tools"), ("orchestrator", "coding", "heavy"), 5800, 50),
        )
        for model_id, caps, tags, latency_ms, priority in openrouter_rows:
            canonical = model_id.removesuffix(":free").replace("nvidia/", "nvidia:")
            rows.append(
                _free_card(
                    "openrouter",
                    model_id,
                    canonical_id=canonical,
                    capabilities=caps,
                    tags=("verified", "reliable", *tags),
                    latency_ms=latency_ms,
                    priority=priority,
                    quality_class="high" if "heavy" in tags else "balanced",
                    billing_mode="free-route",
                )
            )

    if provider_is_configured("ollama"):
        ollama_rows = (
            ("gemma4:31b", ("text", "reasoning", "coding", "tools"), ("orchestrator", "coding"), 375, 14),
            ("gpt-oss:120b", ("text", "reasoning", "coding", "tools"), ("orchestrator", "coding", "heavy"), 612, 17),
            ("nemotron-3-super", ("text", "reasoning", "coding"), ("coding", "heavy"), 1249, 27),
            ("nemotron-3-nano:30b", ("text", "reasoning", "coding"), ("coding", "small"), 1294, 28),
            ("gpt-oss:20b", ("text", "reasoning", "coding", "tools"), ("orchestrator", "coding", "small"), 1381, 23),
            ("minimax-m3", ("text", "reasoning", "coding"), ("coding", "heavy"), 2066, 39),
            ("nemotron-3-ultra", ("text", "reasoning", "coding"), ("coding", "heavy"), 29065, 90),
        )
        for model_id, caps, tags, latency_ms, priority in ollama_rows:
            canonical = {
                "gpt-oss:120b": "openai:gpt-oss-120b",
                "gpt-oss:20b": "openai:gpt-oss-20b",
                "nemotron-3-super": "nvidia:nemotron-3-super-120b-a12b",
                "nemotron-3-nano:30b": "nvidia:nemotron-3-nano-30b-a3b",
                "nemotron-3-ultra": "nvidia:nemotron-3-ultra-550b-a55b",
            }.get(model_id, f"ollama:{model_id}")
            rows.append(
                _free_card(
                    "ollama",
                    model_id,
                    canonical_id=canonical,
                    capabilities=caps,
                    tags=("verified", "reliable", *tags),
                    latency_ms=latency_ms,
                    priority=priority,
                    quality_class="high" if "heavy" in tags else "balanced",
                )
            )

    return rows


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
                "default,orchestrator,fast,free,tools",
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
    openrouter_free = route.provider == "openrouter" and (
        route.primary.endswith(":free") or route.primary == "stealth/ox-alpha"
    )
    return ModelResource(
        id=route.primary,
        provider=route.provider,
        capabilities=capabilities,
        free=free,
        priority=0,
        name="Ox Alpha" if route.primary == "stealth/ox-alpha" else route.primary,
        tags=frozenset(tags),
        latency_class=os.getenv(
            "OPERLY_ORCHESTRATOR_LATENCY_CLASS", "fast"
        ).strip().lower()
        or None,
        cost_class="free" if free else None,
        quality_class=os.getenv(
            "OPERLY_ORCHESTRATOR_QUALITY_CLASS", ""
        ).strip().lower()
        or None,
        locality=os.getenv(
            "OPERLY_ORCHESTRATOR_LOCALITY", "remote"
        ).strip().lower()
        or None,
        canonical_id=route.primary.removesuffix(":free"),
        billing_mode="free-route" if openrouter_free else ("free-tier" if free else None),
        input_cost_per_million=0.0 if openrouter_free else None,
        output_cost_per_million=0.0 if openrouter_free else None,
        verified_latency_ms=5800 if route.primary == "stealth/ox-alpha" else None,
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
                latency_class=str(item.get("latency_class") or "").strip().lower()
                or None,
                cost_class=str(item.get("cost_class") or "").strip().lower() or None,
                quality_class=str(item.get("quality_class") or "").strip().lower()
                or None,
                locality=str(item.get("locality") or "").strip().lower() or None,
                canonical_id=str(item.get("canonical_id") or model_id),
                billing_mode=str(item.get("billing_mode") or "").strip().lower()
                or None,
                input_cost_per_million=_optional_float(
                    item.get("input_cost_per_million")
                ),
                output_cost_per_million=_optional_float(
                    item.get("output_cost_per_million")
                ),
                verified_latency_ms=(
                    int(item["verified_latency_ms"])
                    if item.get("verified_latency_ms") is not None
                    else None
                ),
            )
        )
    return resources


def model_resources() -> tuple[ModelResource, ...]:
    """Return orchestrator + verified/configured/discovered/registered resources."""
    orchestrator = _normalize(_orchestrator_resource())
    merged: dict[tuple[str, str], ModelResource] = {}
    for resource in _verified_free_resources():
        normalized = _normalize(resource)
        merged[(normalized.provider, normalized.id)] = normalized
    merged[(orchestrator.provider, orchestrator.id)] = orchestrator
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
            key=lambda item: (
                item.priority,
                not item.free,
                item.verified_latency_ms or 10**9,
                item.provider,
                item.id,
            ),
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
            item.verified_latency_ms or 10**9,
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
