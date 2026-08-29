"""Capability-aware state projection for Agent Runtime v2.

Raw verified observations remain durable in RunState. Disposable workers receive a
bounded semantic projection, and downstream stages prefer accepted StepOutput over
replaying provider payloads.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any, Iterable

from .contracts import Observation, RunState, Step, StepState

_CONTROL_KEYS = frozenset(
    {"action_id", "approval_id", "authority", "principal_id", "owner_id", "scope_id"}
)
_IDENTITY_KEYS = frozenset(
    {
        "id", "ids", "ref", "refs", "ranked_refs", "message_id", "message_ids",
        "thread_id", "thread_ids", "event_id", "event_ids", "task_id", "task_ids",
        "artifact_id", "contact_id", "draft_id", "attachment_id", "external_reference",
        "materialized_refs", "next_page_token", "page_token",
    }
)
_CONTENT_KEYS = frozenset(
    {
        "source", "sources", "provider", "type", "kind", "name", "title", "subject",
        "snippet", "preview", "summary", "content", "body", "text", "from", "sender",
        "to", "cc", "date", "timestamp", "created_at", "updated_at", "start", "end",
        "start_time", "end_time", "attendees", "status", "count", "query", "url", "uri",
        "timezone", "estimated_tokens", "estimated_tokens_if_all_materialized",
        "contents_materialized", "federated", "returned_count", "requested_limit",
        "result_size_estimate", "truncated",
    }
)
_CONTAINER_KEYS = frozenset(
    {"observation", "results", "items", "messages", "threads", "events", "tasks", "contexts", "references", "matches", "data", "evidence"}
)


def _is_identity_key(key: str) -> bool:
    clean = str(key or "").strip().lower()
    if clean in _CONTROL_KEYS:
        return False
    return (
        clean in _IDENTITY_KEYS
        or clean.endswith("_id")
        or clean.endswith("_ids")
        or clean.endswith("_ref")
        or clean.endswith("_refs")
    )


def _scalar(value: Any, *, key: str) -> Any:
    if not isinstance(value, str):
        return value
    if _is_identity_key(key):
        return value
    clean = key.lower()
    if clean in {"content", "body", "text"}:
        return value[:1_000]
    if clean in {"snippet", "preview", "summary"}:
        return value[:500]
    return value[:400]


def _project_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return _scalar(value, key=key)
    if depth >= 5:
        return None
    if isinstance(value, list):
        limit = 20 if _is_identity_key(key) or key in {"refs", "ranked_refs"} else 10
        rows = [_project_value(item, key=key, depth=depth + 1) for item in value[:limit]]
        return [item for item in rows if item is not None]
    if not isinstance(value, dict):
        return str(value)[:400]

    output: dict[str, Any] = {}
    for raw_key, item in value.items():
        clean = str(raw_key)
        lowered = clean.lower()
        if lowered in _CONTROL_KEYS:
            continue
        if _is_identity_key(clean):
            projected = _project_value(item, key=clean, depth=depth + 1)
            if projected is not None:
                output[clean] = projected
    for raw_key, item in value.items():
        clean = str(raw_key)
        lowered = clean.lower()
        if clean in output or lowered in _CONTROL_KEYS:
            continue
        if lowered in _CONTENT_KEYS or lowered in _CONTAINER_KEYS:
            projected = _project_value(item, key=lowered, depth=depth + 1)
            if projected not in (None, {}, []):
                output[clean] = projected
    return output


def _gmail_search_projection(observation: dict[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key in (
        "query", "next_page_token", "page_token", "returned_count", "requested_limit",
        "result_size_estimate", "truncated",
    ):
        if key in observation:
            projected[key] = _scalar(observation[key], key=key)
    messages = observation.get("messages")
    if isinstance(messages, list):
        compact_messages: list[dict[str, Any]] = []
        for row in messages[:50]:
            if not isinstance(row, dict):
                continue
            compact: dict[str, Any] = {}
            for key in ("id", "thread_id", "date", "from", "to", "subject"):
                if row.get(key) is not None:
                    compact[key] = _scalar(row.get(key), key=key)
            if row.get("snippet"):
                compact["snippet"] = str(row.get("snippet"))[:220]
            if isinstance(row.get("label_ids"), list):
                compact["label_ids"] = [str(item)[:80] for item in row["label_ids"][:8]]
            compact_messages.append(compact)
        projected["messages"] = compact_messages
        projected.setdefault("returned_count", len(messages))
    return projected


def project_result(capability_id: str, result: dict[str, Any]) -> dict[str, Any]:
    result = dict(result or {})
    projected: dict[str, Any] = {}
    for key in ("ok", "success", "status", "plugin", "retryable", "changed", "error", "reason"):
        if key in result:
            projected[key] = _scalar(result[key], key=key)
    verification = result.get("verification")
    if isinstance(verification, dict):
        projected["verification"] = {
            key: verification[key]
            for key in ("success", "changed")
            if key in verification
        }

    observation = result.get("observation")
    observation = observation if isinstance(observation, dict) else {}
    if capability_id == "gmail.search" and observation:
        projected["observation"] = _gmail_search_projection(observation)
    elif observation:
        body = _project_value(observation, key="observation")
        if body:
            projected["observation"] = body

    # Test providers and a few internal capabilities return evidence at the root.
    root_projection = _project_value(result)
    for key, value in root_projection.items():
        if key not in projected and key not in {"verification", "observation"}:
            projected[key] = value

    if capability_id == "context.search":
        obs = projected.get("observation")
        if isinstance(obs, dict):
            ranked = obs.get("ranked_refs")
            refs = obs.get("refs")
            if ranked:
                projected["usable_refs"] = copy.deepcopy(ranked)
            elif isinstance(refs, list):
                projected["usable_refs"] = [
                    row.get("id") or row.get("ref")
                    for row in refs
                    if isinstance(row, dict) and (row.get("id") or row.get("ref"))
                ]
    return projected


def _successful(result: dict[str, Any]) -> bool:
    if result.get("ok") is False or result.get("success") is False:
        return False
    status = str(result.get("status") or result.get("lifecycle_status") or "").upper()
    if status in {"DENIED", "FAILED", "UNVERIFIED", "CANCELLED", "EXPIRED", "VERIFICATION_FAILED", "INVALID_ARGUMENTS", "INCOMPLETE_COVERAGE", "MISSING_READ_EVIDENCE"}:
        return False
    verification = result.get("verification")
    if isinstance(verification, dict) and verification.get("success") is True:
        return True
    return bool(
        result.get("verified") is True
        or result.get("success") is True
        or result.get("ok") is True
        or status in {"VERIFIED", "SUCCESS", "SUCCEEDED", "COMPLETED"}
    )


def current_observations(
    observations: Iterable[Observation],
    *,
    max_items: int = 6,
    successes_per_capability: int = 2,
) -> list[Observation]:
    rows = list(observations)
    if not rows:
        return []
    last_by_signature: dict[str, tuple[int, Observation]] = {}
    for index, item in enumerate(rows):
        last_by_signature[item.signature] = (index, item)
    deduped = sorted(last_by_signature.values(), key=lambda pair: pair[0])

    successes: dict[str, list[tuple[int, Observation]]] = defaultdict(list)
    latest_failure: dict[str, tuple[int, Observation]] = {}
    latest_success_index: dict[str, int] = {}
    for index, item in deduped:
        if _successful(item.result):
            successes[item.capability_id].append((index, item))
            latest_success_index[item.capability_id] = index
        else:
            latest_failure[item.capability_id] = (index, item)

    selected: list[tuple[int, Observation]] = []
    for capability_id, values in successes.items():
        selected.extend(values[-max(1, successes_per_capability):])
        failure = latest_failure.get(capability_id)
        if failure and failure[0] > latest_success_index.get(capability_id, -1):
            selected.append(failure)
    for capability_id, failure in latest_failure.items():
        if capability_id not in successes:
            selected.append(failure)
    selected.sort(key=lambda pair: pair[0])
    return [item for _, item in selected[-max(1, max_items):]]


class RuntimeV2ProjectedEngineMixin:
    @staticmethod
    def _observation_payload(item: Observation) -> dict[str, Any]:
        return {
            "capability_id": item.capability_id,
            "arguments": copy.deepcopy(item.arguments),
            "result": project_result(item.capability_id, item.result),
            "memoized": item.memoized,
        }

    @classmethod
    def _working_payload(cls, step_state: StepState) -> list[dict[str, Any]]:
        return [
            cls._observation_payload(item)
            for item in current_observations(step_state.observations, max_items=6)
        ]

    @classmethod
    def _dependency_payload(cls, state: RunState, step: Step) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for dependency_id in step.depends_on:
            dependency = state.steps.get(dependency_id)
            if dependency is None:
                continue
            if dependency.output is not None:
                payload[dependency_id] = {
                    "status": dependency.status,
                    "output": dependency.output.as_dict(),
                }
                continue
            payload[dependency_id] = {
                "status": dependency.status,
                "summary": dependency.summary[:3_000],
                "observations": [
                    cls._observation_payload(item)
                    for item in current_observations(
                        dependency.observations,
                        max_items=2,
                        successes_per_capability=1,
                    )
                ],
            }
        return payload
