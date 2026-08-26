"""Coding-model boundary over the shared Model.infer runtime.

The coding harness never sees a provider route. A temporary chat adapter preserves
the existing persistent tool-loop interface while provider/model selection, retry,
and cross-provider failover live entirely inside ``packages.model_runtime``.
"""
from __future__ import annotations

import json
import os
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


_MUTATION_TOOLS = frozenset({"write", "edit", "remove", "copy", "move", "patch"})


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


def _tool_call_names(message: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for call in message.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _tool_payload(message: dict[str, Any]) -> dict[str, Any] | None:
    raw = message.get("content")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _latest_failed_finish(messages: list[dict[str, Any]]) -> tuple[int, dict[str, Any]] | None:
    """Return the newest rejected deterministic finish gate, if any."""

    for index in range(len(messages) - 1, -1, -1):
        item = messages[index]
        if item.get("role") != "tool" or str(item.get("tool_name") or "") != "finish":
            continue
        payload = _tool_payload(item)
        if payload is None:
            return None
        if payload.get("ok") is False:
            return index, payload
        return None
    return None


def _has_mutation_after(messages: list[dict[str, Any]], index: int) -> bool:
    return any(
        item.get("role") == "assistant" and bool(_tool_call_names(item) & _MUTATION_TOOLS)
        for item in messages[index + 1 :]
    )


def _repair_packet(payload: dict[str, Any]) -> str:
    """Compact a large validator response into a model-actionable repair packet."""

    audit = payload.get("objectiveAudit")
    audit = audit if isinstance(audit, dict) else {}
    compact = {
        "finishError": str(payload.get("error") or "deterministic finish validation failed")[:1200],
        "objectiveMessage": str(audit.get("message") or "")[:1200],
        "behaviorGaps": list(audit.get("behaviorGaps") or [])[:8],
        "unmetRequirements": list(audit.get("unmetRequirements") or [])[:8],
        "runtimeContractGaps": list(audit.get("runtimeContractGaps") or [])[:8],
    }
    text = json.dumps(compact, ensure_ascii=False, default=str)
    return text[:6000]


def _diagnostic(label: str, detail: str) -> None:
    if os.getenv("OPERLY_CODING_DIAGNOSTICS", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    print(f"[coding-model] {label}: {detail[:7000]}", flush=True)


class SemanticFailoverCodingClient:
    """Keep persistent coding sessions moving across model/provider failures.

    A provider call can technically succeed while its model ignores tools, stops in
    prose, or repeatedly asks ``finish`` after the deterministic gate has rejected the
    unchanged workspace. Those are semantic candidate failures, not valid task
    completions. The shared ModelPool can try another compatible provider/model with
    the same bounded coding context rather than making the whole SoftwareProject job
    restart.
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

        failed_finish = _latest_failed_finish(messages)
        needs_finish_repair = bool(
            failed_finish is not None
            and not _has_mutation_after(messages, failed_finish[0])
        )
        effective_messages = list(messages)

        # A greenfield build should not need one inference request per file. Modern
        # tool-capable models can emit multiple independent writes in one assistant
        # turn, which preserves rate-limit headroom for actual validation/repair.
        if schemas and not _history_has_tool_calls(messages):
            effective_messages.append(
                {
                    "role": "user",
                    "content": (
                        "Execution efficiency: for a greenfield implementation, create the smallest coherent complete project first. "
                        "When the provider supports parallel/multiple tool calls, batch independent write calls in the same response "
                        "instead of spending one model turn per file. Include the real application behavior, required Operly runtime/interaction metadata, "
                        "and executable tests in that first implementation pass. Then use deterministic finish evidence for targeted repair."
                    ),
                }
            )

        if needs_finish_repair and failed_finish is not None:
            packet = _repair_packet(failed_finish[1])
            _diagnostic("finish-repair", packet)
            effective_messages.append(
                {
                    "role": "user",
                    "content": (
                        "The deterministic finish gate just rejected this exact workspace. "
                        "Do NOT call finish again until you make a concrete source mutation that addresses the reported gap. "
                        "Use write/edit/remove now; do not reread unchanged files unless the repair packet names an unknown location. "
                        "Preserve behavior that already passes. Repair packet:\n"
                        + packet
                    ),
                }
            )

        while True:
            response = await self.context.chat(effective_messages, schemas)
            names = _tool_call_names(response)

            # A model that immediately repeats finish against an unchanged workspace
            # is stuck. Reject only that candidate and let the provider-diverse pool
            # try a different coding model before spending another outer agent turn.
            if needs_finish_repair and "finish" in names and not (names & _MUTATION_TOOLS):
                result = getattr(self.adapter, "last_result", None)
                resource_id = str(getattr(result, "model_resource_id", "") or "")
                if resource_id and resource_id not in rejected_resources:
                    rejected_resources.add(resource_id)
                    _diagnostic("reject-nonconvergent", resource_id)
                    if reject_model_result(
                        getattr(self.adapter, "model", None),
                        result,
                        classification="non_convergent_finish",
                        detail=(
                            "Coding model repeated finish after deterministic validation rejected "
                            "the unchanged workspace instead of applying the supplied targeted repair"
                        ),
                    ):
                        continue
                return response

            if not schemas or response.get("tool_calls"):
                return response

            requires_tool_progress = (
                not _history_has_tool_calls(messages)
                or _explicit_tool_progress_nudge(effective_messages)
                or needs_finish_repair
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
    fallback. Quota, credits, rate limits, provider failures, and semantic coding-loop
    stalls can therefore fail over without restarting the user's coding job.
    """

    adapter = model_chat_client_for_requirements(
        _coding_requirements(),
        budget=budget or _default_coding_budget(),
        fallback_role=role,
    )
    return SemanticFailoverCodingClient(adapter)
