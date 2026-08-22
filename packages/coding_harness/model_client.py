"""Coding-model provider boundary.

The coding harness depends only on OPERLY's shared chat contract. Provider and
model selection live in the model-runtime plugin registry, so switching models
does not change workspace, execution, repair, or context-window semantics.
"""
from __future__ import annotations

from typing import Any, Protocol

from packages.coding_harness.context_window import ContextBoundCodingClient
from packages.model_runtime import model_client_for_route
from packages.model_runtime.portfolio import model_route


class CodingModelClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


def coding_model_client(role: str = "coding") -> CodingModelClient:
    """Return the configured model/provider plugin behind bounded session context.

    There is intentionally no model-id or provider allowlist here. Coding and
    repair use the same provider-agnostic model routing contract as every other
    Operly harness role; changing the model or provider is configuration, not a
    code authorization event.
    """
    return ContextBoundCodingClient(model_client_for_route(model_route(role)))
