"""Compatibility facade for the shared OPERLY model runtime.

The business brain historically imported ``OllamaClient`` from this module. Keep
that name during migration, but resolve the actual backend through the model
provider registry so the business harness is not coupled to Ollama.
"""
from __future__ import annotations

import os

from packages.model_runtime.ollama_client import OllamaError
from packages.model_runtime.portfolio import ModelRoute
from packages.model_runtime.providers import model_client_for_route


def _configured_provider() -> str:
    explicit = os.getenv("OPERLY_MODEL_BUSINESS_AGENT_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if any(
        os.getenv(name, "").strip()
        for name in ("OPEN_ROUTER_API", "OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY")
    ):
        return "openrouter"
    return "ollama"


class OllamaClient:
    """Legacy constructor delegating to the selected model-provider plugin."""

    def __new__(cls, *, model=None, fallback_models=None):
        route = ModelRoute(
            provider=_configured_provider(),
            primary=str(model or os.getenv("OLLAMA_MODEL", "gemma4:31b")).strip(),
            fallbacks=tuple(fallback_models or ()),
        )
        return model_client_for_route(route)


__all__ = ["OllamaClient", "OllamaError"]
