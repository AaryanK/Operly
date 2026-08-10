"""Deterministic context-window control for persistent coding sessions.

Source files are the durable truth. Old read/grep/web observations are disposable
cache and should not grow the model prompt forever. This wrapper preserves the
session contract and recent coherent turns, drops stale observations once a
bounded character budget is exceeded, and asks the agent to re-read current source
when needed. No summarization LLM call is introduced.
"""
from __future__ import annotations

import json
import os
from typing import Any


COMPACTION_MARKER = (
    "OPERLY compacted older coding-session observations to control context growth. "
    "The current project workspace is authoritative. Re-inspect source with "
    "list/glob/read/grep/diff before relying on omitted observations. Do not infer "
    "file contents from this marker."
)


def _message_chars(message: dict[str, Any]) -> int:
    try:
        return len(json.dumps(message, ensure_ascii=False, default=str))
    except Exception:
        return len(str(message))


def _total_chars(messages: list[dict[str, Any]]) -> int:
    return sum(_message_chars(message) for message in messages)


def _configured_limit() -> int:
    try:
        configured = int(os.getenv("OPERLY_CODING_CONTEXT_CHARS", "96000"))
    except ValueError:
        configured = 96_000
    return max(32_000, min(configured, 400_000))


def _configured_tail() -> int:
    try:
        configured = int(os.getenv("OPERLY_CODING_CONTEXT_RECENT_MESSAGES", "14"))
    except ValueError:
        configured = 14
    return max(4, min(configured, 40))


def compact_messages(
    messages: list[dict[str, Any]],
    *,
    limit_chars: int | None = None,
    recent_messages: int | None = None,
) -> list[dict[str, Any]]:
    """Return a bounded history while preserving session authority and recent turns.

    The first system message and first user packet contain the coding policy,
    approved specification and task. They are always retained. When trimming the
    tail, start at a non-tool message so an orphaned tool result is never presented
    without the assistant turn that requested it.
    """
    if len(messages) <= 2:
        return list(messages)
    limit = _configured_limit() if limit_chars is None else max(8_000, int(limit_chars))
    if _total_chars(messages) <= limit:
        return list(messages)

    head = list(messages[:2])
    tail_count = _configured_tail() if recent_messages is None else max(2, int(recent_messages))
    start = max(2, len(messages) - tail_count)

    # Tool responses belong to a preceding assistant tool-call turn. Never begin
    # compacted history with a bare tool response.
    while start < len(messages) and str(messages[start].get("role") or "") == "tool":
        start += 1
    if start >= len(messages):
        start = max(2, len(messages) - 2)
        while start > 2 and str(messages[start].get("role") or "") == "tool":
            start -= 1

    tail = list(messages[start:])
    marker = {"role": "user", "content": COMPACTION_MARKER}
    compacted = [*head, marker, *tail]

    # If recent tool output alone is huge, drop the oldest complete recent turns
    # until the bounded context fits. The authoritative first two messages stay.
    while len(compacted) > 4 and _total_chars(compacted) > limit:
        body = compacted[3:]
        remove_through = 1
        # Remove an assistant turn together with its following tool results.
        if body and str(body[0].get("role") or "") == "assistant":
            remove_through = 1
            while remove_through < len(body) and str(body[remove_through].get("role") or "") == "tool":
                remove_through += 1
        del compacted[3 : 3 + remove_through]

    return compacted


class ContextBoundCodingClient:
    """Provider-neutral chat wrapper that bounds stale coding observations."""

    def __init__(self, inner) -> None:
        self.inner = inner

    async def chat(self, messages: list[dict[str, Any]], tools=None):
        return await self.inner.chat(compact_messages(messages), tools)
