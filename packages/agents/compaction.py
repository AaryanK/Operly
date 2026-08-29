"""Deterministic working-context compaction for long agent loops.

Raw observations remain available in AgentRuntime.trace / durable run state. This
module only shrinks old tool-message payloads that would otherwise be replayed to the
model on every subsequent step.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentCompactionPolicy:
    max_working_chars: int = 48_000
    keep_recent_tool_messages: int = 4
    compact_tool_chars: int = 1400

    def normalized(self) -> "AgentCompactionPolicy":
        return AgentCompactionPolicy(
            max_working_chars=max(8_000, int(self.max_working_chars)),
            keep_recent_tool_messages=max(1, int(self.keep_recent_tool_messages)),
            compact_tool_chars=max(300, int(self.compact_tool_chars)),
        )


@dataclass(frozen=True, slots=True)
class CompactionResult:
    before_chars: int
    after_chars: int
    compacted_tool_messages: int

    @property
    def saved_chars(self) -> int:
        return max(0, self.before_chars - self.after_chars)

    @property
    def approx_saved_tokens(self) -> int:
        return self.saved_chars // 4


def serialized_chars(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(value))


def approx_tokens(value: Any) -> int:
    """Cheap trace/debug estimate. Provider usage remains billing truth."""
    return max(1, serialized_chars(value) // 4) if value else 0


_RECORD_KEYS = (
    "id",
    "message_id",
    "thread_id",
    "resource_id",
    "name",
    "email",
    "from",
    "to",
    "cc",
    "subject",
    "date",
    "timestamp",
    "internal_date",
    "start",
    "end",
    "status",
    "summary",
    "snippet",
)
_READ_COLLECTION_KEYS = (
    "messages",
    "threads",
    "events",
    "items",
    "results",
    "records",
    "tasks",
    "contacts",
)


def _bounded_record(value: Any) -> Any:
    """Keep enough metadata from a read result for the next reasoning turn."""

    if not isinstance(value, dict):
        return str(value)[:180]
    output: dict[str, Any] = {}
    for key in _RECORD_KEYS:
        if key not in value:
            continue
        item = value[key]
        if isinstance(item, (dict, list)):
            output[key] = _bounded_value(item, depth=2)
        elif item is not None:
            limit = 240 if key in {"summary", "snippet"} else 160
            output[key] = str(item)[:limit]
        if len(output) >= 10:
            break
    if not output:
        output["keys"] = list(value)[:12]
    return output


def _bounded_observation(value: Any) -> Any:
    """Preserve a bounded sample of provider read/search observations.

    Factory workers reset their visible transcript after every tool round. Dropping a
    Gmail/Calendar search down to only `{status: VERIFIED}` makes the next worker turn
    unable to reason over the result and encourages identical re-queries. Keep locators
    and short snippets for a few records while raw payloads remain only in the trace.
    """

    if not isinstance(value, dict):
        return _bounded_value(value, depth=1)
    output: dict[str, Any] = {}
    for key in ("count", "next_cursor", "has_more", "calendar_id", "query"):
        if key in value:
            output[key] = _bounded_value(value[key], depth=1)
    for key in _READ_COLLECTION_KEYS:
        rows = value.get(key)
        if not isinstance(rows, list):
            continue
        output[key] = {
            "item_count": len(rows),
            "sample": [_bounded_record(item) for item in rows[:5]],
        }
        break
    if not output:
        # Some providers nest the useful read collection one level deeper.
        for key, item in list(value.items())[:20]:
            if isinstance(item, dict) and any(
                isinstance(item.get(collection), list)
                for collection in _READ_COLLECTION_KEYS
            ):
                output[str(key)] = _bounded_observation(item)
                break
    if not output:
        output["keys"] = list(value)[:20]
        output["field_count"] = len(value)
    return output


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        if isinstance(value, (dict, list)):
            return f"<{type(value).__name__}:{len(value)}>"
        return str(value)[:240]
    if isinstance(value, dict):
        # Preserve state/evidence handles and lifecycle facts; collapse large record
        # bodies that can be re-retrieved by capability/context/artifact reference.
        priority_keys = (
            "ok",
            "success",
            "status",
            "reason",
            "error",
            "plugin",
            "capability_id",
            "action_id",
            "approval_id",
            "resource_id",
            "count",
            "ref",
            "refs",
            "context_refs_used",
            "artifact_id",
            "artifact_ids",
            "evidence_refs",
            "next_cursor",
            "has_more",
            "retryable",
        )
        output: dict[str, Any] = {}
        for key in priority_keys:
            if key in value:
                output[key] = _bounded_value(value[key], depth=depth + 1)
        for key, item in value.items():
            if key in output:
                continue
            lowered = str(key).lower()
            if lowered == "observation":
                output[str(key)] = _bounded_observation(item)
            elif lowered.endswith(("_id", "_ids", "_ref", "_refs")):
                output[str(key)] = _bounded_value(item, depth=depth + 1)
            elif lowered in {"summary", "message", "verification", "lifecycle"}:
                output[str(key)] = _bounded_value(item, depth=depth + 1)
            if len(output) >= 18:
                break
        if not output:
            output["keys"] = list(value)[:20]
            output["field_count"] = len(value)
        return output
    if isinstance(value, list):
        if len(value) <= 6:
            return [_bounded_value(item, depth=depth + 1) for item in value]
        return {
            "item_count": len(value),
            "sample": [_bounded_value(item, depth=depth + 1) for item in value[:3]],
        }
    if isinstance(value, str):
        return value[:500]
    return value


def compact_tool_content(content: str, *, max_chars: int) -> str:
    raw = str(content or "")
    if len(raw) <= max_chars:
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw[:max_chars] + "… [compacted]"
    compacted_payload = {
        "_operly_compacted": True,
        "summary": _bounded_value(parsed),
        "raw_chars": len(raw),
        "note": "Full observation remains in the run trace and may be re-retrieved by reference.",
    }
    compacted = json.dumps(
        compacted_payload,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    if len(compacted) <= max_chars:
        return compacted

    # Preserve valid JSON under the hard prompt budget. If the bounded observation is
    # still too large, shrink read samples rather than slicing JSON mid-object.
    summary = compacted_payload.get("summary")
    if isinstance(summary, dict):
        observation = summary.get("observation")
        if isinstance(observation, dict):
            for value in observation.values():
                if isinstance(value, dict) and isinstance(value.get("sample"), list):
                    value["sample"] = value["sample"][:2]
    compacted = json.dumps(
        compacted_payload,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    if len(compacted) <= max_chars:
        return compacted

    fallback = {
        "_operly_compacted": True,
        "summary": _bounded_value(parsed),
        "raw_chars": len(raw),
    }
    # Remove optional note and progressively reduce summary while always returning JSON.
    if isinstance(fallback["summary"], dict):
        fallback["summary"].pop("verification", None)
        fallback["summary"].pop("lifecycle", None)
    compacted = json.dumps(fallback, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(compacted) <= max_chars:
        return compacted
    return json.dumps(
        {
            "_operly_compacted": True,
            "summary": {
                "status": parsed.get("status") if isinstance(parsed, dict) else None,
                "plugin": parsed.get("plugin") if isinstance(parsed, dict) else None,
                "observation": _bounded_observation(
                    parsed.get("observation") if isinstance(parsed, dict) else {}
                ),
            },
            "raw_chars": len(raw),
        },
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )[:max_chars]


def compact_working_messages(
    messages: list[dict[str, Any]],
    policy: AgentCompactionPolicy | None = None,
) -> CompactionResult:
    normalized = (policy or AgentCompactionPolicy()).normalized()
    before = serialized_chars(messages)
    if before <= normalized.max_working_chars:
        return CompactionResult(before, before, 0)

    tool_indexes = [
        index
        for index, message in enumerate(messages)
        if str(message.get("role") or "") == "tool"
    ]
    compactable = tool_indexes[: -normalized.keep_recent_tool_messages]
    compacted_count = 0

    # Oldest observations are compacted first. Stop once the working prompt is under
    # budget so recent/raw evidence remains available whenever possible.
    for index in compactable:
        message = messages[index]
        original = str(message.get("content") or "")
        compacted = compact_tool_content(
            original,
            max_chars=normalized.compact_tool_chars,
        )
        if compacted == original:
            continue
        message["content"] = compacted
        compacted_count += 1
        if serialized_chars(messages) <= normalized.max_working_chars:
            break

    after = serialized_chars(messages)
    return CompactionResult(before, after, compacted_count)
