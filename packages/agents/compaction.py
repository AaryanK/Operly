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
            if lowered.endswith(("_id", "_ids", "_ref", "_refs")):
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
    compacted = json.dumps(
        {
            "_operly_compacted": True,
            "summary": _bounded_value(parsed),
            "raw_chars": len(raw),
            "note": "Full observation remains in the run trace and may be re-retrieved by reference.",
        },
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    return compacted[:max_chars]


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
