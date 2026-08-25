"""Bounded semantic rescue when an agent is about to give up without acting.

Progressive capability exposure deliberately starts models with a tiny kernel. This
module closes the corresponding reliability gap: if a non-trivial objective reaches
a terminal model response without any real capability evidence, the controller may
perform one governed semantic discovery pass and expose promising schemas before
letting the model conclude that Operly cannot do the work.

The rescue never routes by keywords, never grants authority, and never executes the
discovered business capability itself. Search/describe still cross the canonical
capability firewall; the model gets the next execution decision.
"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable
from uuid import uuid4

from packages.agents.runtime import AgentTraceEntry
from packages.model_runtime.conversation_policy import is_trivial_conversation


CapabilityInvoker = Callable[
    [str, dict[str, Any], str | None],
    Awaitable[dict[str, Any]] | dict[str, Any],
]
ObservationHook = Callable[
    [str, dict[str, Any], dict[str, Any]],
    Awaitable[None] | None,
]


# Metadata/reasoning helpers are not proof that the root operational objective ran.
_META_CAPABILITIES = frozenset(
    {
        "capability.search",
        "capability.describe",
        "event.search",
        "event.describe",
        "context.search",
        "context.get",
        "model.invoke",
        "model.deep_reason",
    }
)

# Lexical overlap is only a retrieval confidence signal, never a router. Semantic-only
# matches remain eligible when the embedding score is strong enough.
_MIN_LEXICAL_RELEVANCE = 0.75
_MIN_SEMANTIC_RELEVANCE = 0.52


@dataclass(frozen=True, slots=True)
class CapabilityRescueResult:
    attempted: bool
    applied: bool
    candidate_ids: tuple[str, ...] = ()
    trace: tuple[AgentTraceEntry, ...] = ()
    reason: str | None = None


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


def has_execution_evidence(trace: Iterable[Any]) -> bool:
    """Return whether a trace contains a non-meta capability observation."""
    for entry in trace:
        capability_id = str(getattr(entry, "capability_id", "") or "").strip()
        if capability_id and capability_id not in _META_CAPABILITIES:
            return True
    return False


def _observation(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("observation")
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _relevant_candidates(payload: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    rows = _observation(payload).get("capabilities") or []
    if not isinstance(rows, list):
        return []

    selected: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        capability_id = str(row.get("id") or "").strip()
        if not capability_id or capability_id in _META_CAPABILITIES:
            continue
        if row.get("authorized") is False:
            continue
        lexical = _number(row.get("lexical_score"))
        semantic = _number(row.get("semantic_score"))
        if lexical < _MIN_LEXICAL_RELEVANCE and semantic < _MIN_SEMANTIC_RELEVANCE:
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def _candidate_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for row in rows[:4]:
        availability = row.get("availability") if isinstance(row.get("availability"), dict) else {}
        summary.append(
            {
                "id": str(row.get("id") or ""),
                "display_name": str(row.get("display_name") or "")[:120],
                "description": str(row.get("description") or "")[:500],
                "available": availability.get("available"),
                "reason": availability.get("reason"),
                "next_action": availability.get("nextAction"),
            }
        )
    return summary


def _install_rescue_message(
    messages: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> None:
    # The terminal assistant prose was never accepted as the run result. Remove it so
    # the next model turn re-evaluates the user's objective rather than anchoring on its
    # own false inability claim.
    if messages and str(messages[-1].get("role") or "") == "assistant" and not (
        messages[-1].get("tool_calls") or []
    ):
        messages.pop()

    messages[:] = [
        message
        for message in messages
        if not bool(message.get("_operly_capability_rescue"))
    ]
    summary = _candidate_summary(rows)
    rescue_message = {
        "role": "system",
        "_operly_capability_rescue": True,
        "content": (
            "OPERLY CAPABILITY RESCUE (application-controlled; not user-authored):\n"
            "The previous response attempted to terminate without operational evidence. "
            "A governed semantic search found capabilities relevant to the current root objective. "
            "Their exact authorized schemas have been requested through capability.describe and will "
            "be present in the next tool surface when executable. Re-evaluate the original request now. "
            "Do not claim a capability is unavailable merely because it was absent from the initial tool list. "
            "If a discovered operation is unavailable because of connector/scope/health state, explain that "
            "specific state instead of claiming Operly cannot perform the class of operation.\n"
            + json.dumps(summary, ensure_ascii=False, default=str)[:6000]
        ),
    }

    # Keep application-controlled guidance before the current user turn for broad
    # provider compatibility rather than appending a synthetic tool message without a
    # matching assistant tool_call.
    insert_at = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        if str(messages[index].get("role") or "") == "user":
            insert_at = index
            break
    messages.insert(insert_at, rescue_message)


async def attempt_capability_rescue(
    *,
    objective: str,
    messages: list[dict[str, Any]],
    invoke: CapabilityInvoker,
    on_observation: ObservationHook | None = None,
    max_candidates: int = 4,
) -> CapabilityRescueResult:
    """Run one semantic search/describe rescue pass without widening authority."""
    clean_objective = str(objective or "").strip()
    if not clean_objective or is_trivial_conversation(clean_objective):
        return CapabilityRescueResult(False, False, reason="trivial_or_empty_objective")

    trace: list[AgentTraceEntry] = []
    search_arguments = {"query": clean_objective[:1000], "limit": 8}
    search_call_id = f"operly-rescue-search-{uuid4()}"
    search_payload = dict(
        await _resolve(invoke("capability.search", search_arguments, search_call_id)) or {}
    )
    search_entry = AgentTraceEntry(
        "capability.search",
        search_arguments,
        search_payload,
        search_call_id,
    )
    trace.append(search_entry)
    if on_observation is not None:
        await _resolve(on_observation("capability.search", search_arguments, search_payload))

    candidates = _relevant_candidates(search_payload, limit=max(1, min(max_candidates, 8)))
    candidate_ids = tuple(str(row.get("id") or "") for row in candidates)
    if not candidate_ids:
        return CapabilityRescueResult(
            True,
            False,
            trace=tuple(trace),
            reason="no_relevant_authorized_capability",
        )

    describe_arguments = {"ids": list(candidate_ids)}
    describe_call_id = f"operly-rescue-describe-{uuid4()}"
    describe_payload = dict(
        await _resolve(invoke("capability.describe", describe_arguments, describe_call_id)) or {}
    )
    describe_entry = AgentTraceEntry(
        "capability.describe",
        describe_arguments,
        describe_payload,
        describe_call_id,
    )
    trace.append(describe_entry)
    if on_observation is not None:
        await _resolve(on_observation("capability.describe", describe_arguments, describe_payload))

    _install_rescue_message(messages, candidates)
    return CapabilityRescueResult(
        True,
        True,
        candidate_ids=candidate_ids,
        trace=tuple(trace),
        reason="semantic_capability_match",
    )
