"""Capability-aware state projection for Agent Runtime v2.

The durable RunState keeps full verified observations for audit/reuse. Disposable
workers receive a small semantic projection instead of generic string compaction.
Identifiers and provider references are never intentionally discarded.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any, Iterable

from .contracts import Observation, RunState, Step, StepState

_IDENTITY_KEYS = frozenset(
    {
        "id",
        "ids",
        "ref",
        "refs",
        "ranked_refs",
        "message_id",
        "message_ids",
        "thread_id",
        "thread_ids",
        "event_id",
        "event_ids",
        "task_id",
        "task_ids",
        "action_id",
        "artifact_id",
        "contact_id",
        "draft_id",
        "attachment_id",
        "external_reference",
        "materialized_refs",
    }
)
_CONTENT_KEYS = frozenset(
    {
        "source",
        "sources",
        "provider",
        "type",
        "kind",
        "name",
        "title",
        "subject",
        "snippet",
        "preview",
        "summary",
        "content",
        "body",
        "text",
        "from",
        "sender",
        "to",
        "cc",
        "date",
        "timestamp",
        "created_at",
        "updated_at",
        "start",
        "end",
        "start_time",
        "end_time",
        "attendees",
        "status",
        "count",
        "query",
        "url",
        "uri",
        "timezone",
        "estimated_tokens",
        "estimated_tokens_if_all_materialized",
        "contents_materialized",
        "federated",
    }
)
_CONTAINER_KEYS = frozenset(
    {
        "observation",
        "results",
        "items",
        "messages",
        "threads",
        "events",
        "tasks",
        "contexts",
        "references",
        "matches",
        "data",
        "evidence",
    }
)


def _is_identity_key(key: str) -> bool:
    clean = str(key or "").strip().lower()
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
        # Provider IDs/refs are opaque and must remain byte-for-byte usable.
        return value
    clean = key.lower()
    if clean in {"content", "body", "text"}:
        return value[:1_200]
    if clean in {"snippet", "preview", "summary"}:
        return value[:700]
    return value[:500]


def _project_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return _scalar(value, key=key)
    if depth >= 5:
        return None
    if isinstance(value, list):
        limit = 20 if _is_identity_key(key) or key in {"refs", "ranked_refs"} else 10
        projected = [
            _project_value(item, key=key, depth=depth + 1)
            for item in value[:limit]
        ]
        return [item for item in projected if item is not None]
    if not isinstance(value, dict):
        return str(value)[:500]

    output: dict[str, Any] = {}
    # Preserve identity-bearing fields first, irrespective of provider shape.
    for raw_key, item in value.items():
        clean = str(raw_key)
        if _is_identity_key(clean):
            projected = _project_value(item, key=clean, depth=depth + 1)
            if projected is not None:
                output[clean] = projected

    for raw_key, item in value.items():
        clean = str(raw_key)
        lowered = clean.lower()
        if clean in output:
            continue
        if lowered in _CONTENT_KEYS or lowered in _CONTAINER_KEYS:
            projected = _project_value(item, key=lowered, depth=depth + 1)
            if projected not in (None, {}, []):
                output[clean] = projected

    # Some providers wrap useful rows under an unanticipated key. Keep that branch
    # only when it itself contains IDs/refs; this is intentionally conservative.
    if depth < 2:
        for raw_key, item in value.items():
            clean = str(raw_key)
            if clean in output or not isinstance(item, (dict, list)):
                continue
            probe = _project_value(item, key=clean, depth=depth + 1)
            if isinstance(probe, dict) and any(_is_identity_key(k) for k in probe):
                output[clean] = probe
            elif isinstance(probe, list) and probe and any(
                isinstance(row, dict) and any(_is_identity_key(k) for k in row)
                for row in probe
            ):
                output[clean] = probe
    return output


def project_result(capability_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Return a worker-safe semantic projection while retaining usable IDs/refs."""

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
    if isinstance(observation, dict):
        projected_observation = _project_value(observation, key="observation")
        if projected_observation:
            projected["observation"] = projected_observation

    # Some test/providers expose their useful rows at the result root rather than
    # under observation. Project those too without copying lifecycle/authority noise.
    root_projection = _project_value(result)
    for key, value in root_projection.items():
        if key not in projected and key not in {"verification", "observation"}:
            projected[key] = value

    if capability_id == "context.search":
        # context.get requires these opaque refs, so make them unmistakable to the
        # next disposable worker even when the provider wraps them deeply.
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
    if status in {"DENIED", "FAILED", "UNVERIFIED", "CANCELLED", "EXPIRED", "VERIFICATION_FAILED", "INVALID_ARGUMENTS"}:
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
    """Coalesce append-only trace observations into small current working state.

    Repeated identical reads collapse by signature. Once a capability succeeds, old
    argument/schema failures for that capability no longer need to be replayed. We
    retain at most a couple of distinct successful reads per capability because
    multiple paginated/search queries can be legitimate working state.
    """

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
    """Drop-in overrides used by RuntimeV2Engine without changing execution logic."""

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
            payload[dependency_id] = {
                "status": dependency.status,
                "summary": dependency.summary[:6_000],
                "observations": [
                    cls._observation_payload(item)
                    for item in current_observations(
                        dependency.observations,
                        max_items=4,
                        successes_per_capability=1,
                    )
                ],
            }
        return payload
