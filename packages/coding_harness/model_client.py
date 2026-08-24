"""Coding-model boundary over the shared Model.infer runtime.

The coding harness never sees a provider route. A temporary chat adapter preserves
the existing persistent tool-loop interface while provider/model selection, retry,
and cross-provider failover live entirely inside ``packages.model_runtime``.
"""
from __future__ import annotations

from typing import Any, Protocol

from packages.coding_harness.context_window import ContextBoundCodingClient
from packages.model_runtime import InferenceBudget, model_chat_client_for_role
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


def coding_model_client(
    role: str = "coding",
    *,
    budget: InferenceBudget | None = None,
) -> CodingModelClient:
    """Return the selected Model behind bounded coding-session context.

    There is intentionally no provider/model allowlist or provider-specific policy
    here. ``budget`` is provider-neutral and is enforced by the model runtime.
    Semantic tool-protocol mismatch also participates in the model pool's failover
    policy instead of making the user retry an otherwise valid generation job.
    """
    adapter = model_chat_client_for_role(role, budget=budget)
    return SemanticFailoverCodingClient(adapter)
