"""Coding-model provider boundary.

The coding harness depends only on OPERLY's shared chat contract. Provider and
model selection live in the model-runtime plugin registry, so switching models
does not change workspace, execution, repair, or context-window semantics.
"""
from __future__ import annotations

import os
from typing import Any, Protocol

from packages.coding_harness.context_window import ContextBoundCodingClient
from packages.model_runtime import model_client_for_route
from packages.model_runtime.portfolio import ModelRoute, model_route


class CodingModelClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


def _coding_allowlist() -> frozenset[str]:
    """Return the owner-authorized model ids for coding/repair execution."""
    raw = os.getenv("OPERLY_CODING_ALLOWED_MODELS", "stealth/ox-alpha")
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
    """Return the configured provider plugin behind bounded session context."""
    route = model_route(role)
    _assert_coding_route_authorized(role, route)
    return ContextBoundCodingClient(model_client_for_route(route))
