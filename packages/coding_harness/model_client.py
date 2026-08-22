"""Coding-model provider boundary.

OPERLY currently uses Ollama for coding-agent turns. The harness depends only on
this tiny chat contract so additional providers can be introduced later without
changing workspace, execution, repair, or context-window semantics.
"""
from __future__ import annotations

import os
from typing import Any, Protocol

from packages.coding_harness.context_window import ContextBoundCodingClient
from packages.model_runtime import OllamaClient
from packages.model_runtime.portfolio import ModelRoute, model_route


class CodingModelClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


def _coding_allowlist() -> frozenset[str]:
    """Return the owner-authorized model ids for coding/repair execution.

    The safe default is the repository's approved Gemma coding model only. Any
    additional cloud model must be deliberately listed in
    OPERLY_CODING_ALLOWED_MODELS before the coding harness may spend requests on it.
    """
    raw = os.getenv("OPERLY_CODING_ALLOWED_MODELS", "gemma4:31b")
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def _assert_coding_route_authorized(role: str, route: ModelRoute) -> None:
    if str(role or "").strip().lower() not in {"coding", "repair"}:
        return
    allowed = _coding_allowlist()
    requested = [route.primary, *route.fallbacks]
    unauthorized = [model for model in requested if model and model not in allowed]
    if unauthorized:
        names = ", ".join(unauthorized)
        raise RuntimeError(
            "Coding model route contains model(s) that are not owner-authorized: "
            f"{names}. Add them to OPERLY_CODING_ALLOWED_MODELS only after explicit approval."
        )


def coding_model_client(role: str = "coding") -> CodingModelClient:
    """Return the configured coding model client behind bounded session context."""
    route = model_route(role)
    _assert_coding_route_authorized(role, route)
    if route.provider != "ollama":
        raise RuntimeError(f"Model provider {route.provider} is not installed")
    return ContextBoundCodingClient(OllamaClient(model=route.primary, fallback_models=route.fallbacks))
