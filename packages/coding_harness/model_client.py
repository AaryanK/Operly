"""Coding-model boundary over the shared Model.infer runtime.

The coding harness never sees a provider route. A temporary chat adapter preserves
the existing persistent tool-loop interface while provider/model selection, retry,
and cross-provider failover live entirely inside ``packages.model_runtime``.
"""
from __future__ import annotations

from typing import Any, Protocol

from packages.coding_harness.context_window import ContextBoundCodingClient
from packages.model_runtime import (
    InferenceBudget,
    ModelRequirements,
    model_chat_client_for_requirements,
)
from packages.model_runtime.semantic_failover import reject_model_result


class CodingModelClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


def _history_has_tool_calls(messages: list[dict[str, Any]]) -> bool:
    return any(
        item.get("role") == "assistant" and bool(item.get("tool_calls"))
        for item in messages
    )


def _explicit_tool_progress_nudge(messages: list[dict[str, Any]]) -> bool:
    for item in reversed(messages):
        if item.get("role") != "user":
            continue
        content = str(item.get("content") or "")
        return content.startswith("Continue with project tools.")
    return False


class SemanticFailoverCodingClient:
    """Treat tool-protocol refusal as a model-candidate failure, not task success.

    Provider calls can succeed while a coding model ignores every supplied tool and
    returns prose. Before any real project-tool progress, that response cannot be a
    valid completion. Reject only that model candidate and let the shared ModelPool
    try another provider/model with the exact same bounded session context.

    Once project tools have run, a no-tool response is returned to the coding agent
    because the agent may legitimately accept it as implicit completion. If the
    agent instead issues its explicit continue-with-tools nudge, a repeated no-tool
    response again becomes safe to classify as a protocol mismatch and fail over.
    """

    def __init__(self, adapter) -> None:
        # Preserve the legacy ContextBoundCodingClient inspection contract used by
        # authorization/diagnostic code: coding clients expose `.inner.model`.
        self.inner = adapter
        self.adapter = adapter
        self.context = ContextBoundCodingClient(adapter)

    @property
    def last_model(self) -> str:
        return str(getattr(self.adapter, "last_model", "unknown"))

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        schemas = list(tools or [])
        rejected_resources: set[str] = set()

        while True:
            response = await self.context.chat(messages, schemas)
            if not schemas or response.get("tool_calls"):
                return response

            requires_tool_progress = (
                not _history_has_tool_calls(messages)
                or _explicit_tool_progress_nudge(messages)
            )
            if not requires_tool_progress:
                return response

            result = getattr(self.adapter, "last_result", None)
            resource_id = str(getattr(result, "model_resource_id", "") or "")
            if not resource_id or resource_id in rejected_resources:
                return response
            rejected_resources.add(resource_id)

            if not reject_model_result(
                getattr(self.adapter, "model", None),
                result,
                classification="tool_protocol_mismatch",
                detail=(
                    "Coding model returned no structured tool calls while the "
                    "persistent coding session still required project-tool progress"
                ),
            ):
                return response


def _default_coding_budget() -> InferenceBudget:
    """Keep each coding turn useful without reserving an oversized completion.

    File-writing tool calls need more output room than normal business-agent turns,
    but a static 4k/16k reservation can cause an otherwise healthy provider to reject
    the request before inference starts. The model pool can fail over across providers,
    so keep one turn bounded and spend additional tokens only on subsequent repair
    turns that actually make progress.
    """

    return InferenceBudget(
        timeout_seconds=75.0,
        attempts_per_model=1,
        max_models=4,
        max_output_tokens=3_000,
    )


def _coding_requirements() -> ModelRequirements:
    """Describe coding-session needs without pinning the harness to one provider."""

    return ModelRequirements(
        requires=frozenset({"text", "tools", "coding"}),
        prefer_tags=frozenset(
            {
                "qualified-coding",
                "qualified-tools",
                "coding",
                "reliable",
                "small",
                "fast",
                "free",
            }
        ),
        avoid_tags=frozenset({"slow"}),
        prefer_free=True,
        max_models=4,
        reason=(
            "persistent project coding session; provider-diverse tool/coding model "
            "pool with bounded per-turn output"
        ),
    )


def coding_model_client(
    role: str = "coding",
    *,
    budget: InferenceBudget | None = None,
) -> CodingModelClient:
    """Return a provider-diverse coding model behind bounded session context.

    The coding harness states concrete requirements (text + tools + coding) instead
    of resolving one provider role up front. The shared model runtime chooses a
    provider-diverse pool, preserving the configured role chain only as a compatible
    fallback. Quota, credits, rate limits, provider failures, and tool-protocol
    mismatches can therefore fail over without restarting the user's coding job.
    """

    adapter = model_chat_client_for_requirements(
        _coding_requirements(),
        budget=budget or _default_coding_budget(),
        fallback_role=role,
    )
    return SemanticFailoverCodingClient(adapter)
