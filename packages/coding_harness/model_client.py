"""Coding-model provider boundary.

OPERLY currently uses Ollama for coding-agent turns. The harness depends only on
this tiny chat contract so additional providers can be introduced later without
changing workspace, execution, repair, or context-window semantics.
"""
from __future__ import annotations

from typing import Any, Protocol

from packages.coding_harness.context_window import ContextBoundCodingClient
from packages.model_runtime import OllamaClient
from packages.model_runtime.portfolio import model_route


class CodingModelClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


def coding_model_client(role: str = "coding") -> CodingModelClient:
    """Return the configured coding model client behind bounded session context."""
    route = model_route(role)
    if route.provider != "ollama":
        raise RuntimeError(f"Model provider {route.provider} is not installed")
    return ContextBoundCodingClient(OllamaClient(model=route.primary, fallback_models=route.fallbacks))
