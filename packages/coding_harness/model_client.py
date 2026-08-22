"""Coding-model boundary over the shared Model.infer runtime.

The coding harness never sees a provider route. A temporary chat adapter preserves
the existing persistent tool-loop interface while provider/model selection, retry,
and cross-provider failover live entirely inside ``packages.model_runtime``.
"""
from __future__ import annotations

from typing import Any, Protocol

from packages.coding_harness.context_window import ContextBoundCodingClient
from packages.model_runtime import InferenceBudget, model_chat_client_for_role


class CodingModelClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


def coding_model_client(
    role: str = "coding",
    *,
    budget: InferenceBudget | None = None,
) -> CodingModelClient:
    """Return the selected Model behind bounded coding-session context.

    There is intentionally no provider/model allowlist or provider-specific policy
    here. ``budget`` is provider-neutral and is enforced by the model runtime.
    """
    return ContextBoundCodingClient(
        model_chat_client_for_role(role, budget=budget)
    )
