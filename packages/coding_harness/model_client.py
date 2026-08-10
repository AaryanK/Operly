"""Coding-model provider boundary.

OPERLY currently uses Ollama for coding-agent turns.  The harness depends only on
this tiny chat contract so additional providers can be introduced later without
changing workspace, execution, or repair semantics.
"""
from __future__ import annotations

from typing import Any, Protocol

from packages.model_runtime import OllamaClient


class CodingModelClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


def coding_model_client() -> CodingModelClient:
    """Return the configured coding model client.

    Ollama is intentionally the only production provider today.  Keeping provider
    selection here prevents provider-specific behavior from leaking into the
    coding harness itself.
    """
    return OllamaClient()
