"""Pluggable provider catalog discovery for model resources."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from packages.model_runtime.catalog import ModelResource, replace_discovered_resources


ModelDiscoverer = Callable[[], Awaitable[list[ModelResource]]]

_DISCOVERERS: dict[str, ModelDiscoverer] = {}
_LAST_REFRESH: dict[str, float] = {}
_LOCK = asyncio.Lock()
_BUILTINS_LOADED = False


def register_model_discoverer(
    provider: str,
    discoverer: ModelDiscoverer,
    *,
    replace: bool = False,
) -> None:
    key = str(provider or "").strip().lower()
    if not key:
        raise ValueError("Model discovery provider is required")
    if key in _DISCOVERERS and not replace:
        raise ValueError(f"Model discoverer already registered: {key}")
    _DISCOVERERS[key] = discoverer


def installed_model_discoverers() -> tuple[str, ...]:
    _ensure_builtin_discoverers()
    return tuple(sorted(_DISCOVERERS))


def _ensure_builtin_discoverers() -> None:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    _BUILTINS_LOADED = True
    # Provider-specific discovery remains outside the harness and catalog core.
    from packages.model_runtime import openrouter_discovery  # noqa: F401
    from packages.model_runtime import provider_discovery  # noqa: F401


async def refresh_model_discovery(
    *,
    provider: str | None = None,
    ttl_seconds: float = 600.0,
    force: bool = False,
) -> dict[str, int]:
    """Refresh every provider-published model catalog available to Operly.

    Discovery failures are isolated per provider. A temporary marketplace/API
    failure must not break the configured orchestrator or erase the last known
    model snapshot.
    """
    _ensure_builtin_discoverers()
    wanted = str(provider or "").strip().lower()
    names = [wanted] if wanted else sorted(_DISCOVERERS)
    now = time.monotonic()
    counts: dict[str, int] = {}
    async with _LOCK:
        for name in names:
            discoverer = _DISCOVERERS.get(name)
            if discoverer is None:
                continue
            last = _LAST_REFRESH.get(name, 0.0)
            if not force and last and now - last < max(0.0, ttl_seconds):
                continue
            try:
                resources = await discoverer()
                replace_discovered_resources(name, resources)
                _LAST_REFRESH[name] = time.monotonic()
                counts[name] = len(resources)
            except Exception:
                # Keep the last known snapshot/configured catalog on transient
                # provider failures. Invocation can still use explicit resources.
                continue
    return counts
