"""Pluggable model-provider registry for OPERLY's shared harnesses.

Harnesses depend on the small ``chat(messages, tools)`` contract. Providers are
registered by name and instantiated from a ``ModelRoute``; adding or replacing a
model backend must not require changes to the business, planning, or coding loops.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from packages.model_runtime.ollama_client import OllamaClient
from packages.model_runtime.openrouter_client import OpenRouterClient
from packages.model_runtime.portfolio import ModelRoute


class ModelClient(Protocol):
    last_model: str

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


ProviderFactory = Callable[[ModelRoute], ModelClient]
_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {}


def register_model_provider(name: str, factory: ProviderFactory, *, replace: bool = False) -> None:
    """Register a provider adapter behind the common OPERLY model contract."""
    key = str(name or "").strip().lower()
    if not key:
        raise ValueError("Model provider name is required")
    if key in _PROVIDER_FACTORIES and not replace:
        raise ValueError(f"Model provider {key} is already registered")
    _PROVIDER_FACTORIES[key] = factory


def model_client_for_route(route: ModelRoute) -> ModelClient:
    """Instantiate the provider selected by a role route."""
    key = str(route.provider or "").strip().lower()
    factory = _PROVIDER_FACTORIES.get(key)
    if factory is None:
        installed = ", ".join(sorted(_PROVIDER_FACTORIES)) or "none"
        raise RuntimeError(
            f"Model provider {key or '<empty>'} is not installed. Installed providers: {installed}"
        )
    return factory(route)


def installed_model_providers() -> tuple[str, ...]:
    return tuple(sorted(_PROVIDER_FACTORIES))


def _ollama_factory(route: ModelRoute) -> ModelClient:
    return OllamaClient(model=route.primary, fallback_models=route.fallbacks)


def _openrouter_factory(route: ModelRoute) -> ModelClient:
    return OpenRouterClient(model=route.primary, fallback_models=route.fallbacks)


register_model_provider("ollama", _ollama_factory)
register_model_provider("openrouter", _openrouter_factory)
