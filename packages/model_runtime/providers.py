"""Pluggable model-provider registry for OPERLY's shared harnesses.

Harnesses depend only on the small chat(messages, tools) contract. Provider-specific
authentication, payload translation, and transport live behind registered adapters.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from packages.model_runtime.ollama_client import OllamaClient
from packages.model_runtime.openai_compatible_client import OpenAICompatibleClient
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


def register_model_provider(
    name: str,
    factory: ProviderFactory,
    *,
    replace: bool = False,
) -> None:
    key = str(name or "").strip().lower()
    if not key:
        raise ValueError("Model provider name is required")
    if key in _PROVIDER_FACTORIES and not replace:
        raise ValueError(f"Model provider {key} is already registered")
    _PROVIDER_FACTORIES[key] = factory


def model_client_for_route(route: ModelRoute) -> ModelClient:
    key = str(route.provider or "").strip().lower()
    factory = _PROVIDER_FACTORIES.get(key)
    if factory is None:
        installed = ", ".join(sorted(_PROVIDER_FACTORIES)) or "none"
        raise RuntimeError(
            f"Model provider {key or '<empty>'} is not installed. "
            f"Installed providers: {installed}"
        )
    return factory(route)


def installed_model_providers() -> tuple[str, ...]:
    return tuple(sorted(_PROVIDER_FACTORIES))


def _ollama_factory(route: ModelRoute) -> ModelClient:
    return OllamaClient(model=route.primary, fallback_models=route.fallbacks)


def _openrouter_factory(route: ModelRoute) -> ModelClient:
    return OpenRouterClient(model=route.primary, fallback_models=route.fallbacks)


def _groq_factory(route: ModelRoute) -> ModelClient:
    return OpenAICompatibleClient(
        provider="groq",
        model=route.primary,
        fallback_models=route.fallbacks,
        default_url="https://api.groq.com/openai/v1/chat/completions",
        api_key_envs=("GROQ_API_KEY", "groq_api_key"),
        env_prefix="GROQ",
    )


def _gemini_factory(route: ModelRoute) -> ModelClient:
    return OpenAICompatibleClient(
        provider="gemini",
        model=route.primary,
        fallback_models=route.fallbacks,
        default_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        api_key_envs=("GEMINI_API_KEY", "gemini_api_key", "GOOGLE_API_KEY"),
        env_prefix="GEMINI",
    )


def _nvidia_factory(route: ModelRoute) -> ModelClient:
    return OpenAICompatibleClient(
        provider="nvidia",
        model=route.primary,
        fallback_models=route.fallbacks,
        default_url="https://integrate.api.nvidia.com/v1/chat/completions",
        api_key_envs=("NVIDIA_API_KEY", "nvidia_api_key"),
        env_prefix="NVIDIA",
    )


register_model_provider("ollama", _ollama_factory)
register_model_provider("openrouter", _openrouter_factory)
register_model_provider("groq", _groq_factory)
register_model_provider("gemini", _gemini_factory)
register_model_provider("nvidia", _nvidia_factory)
