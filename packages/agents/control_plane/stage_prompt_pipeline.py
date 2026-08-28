"""Bounded prompt construction for disposable Operly factory workers.

The Factory owns durable context and raw execution evidence. A worker receives a
small stage envelope once, then a reference-first continuation envelope after each
tool round. Materialized workspace context is intentionally never replayed across
reason-act-observe turns.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from packages.agents.compaction import compact_tool_content, serialized_chars

from .contracts import ContextCapsule, Defect, StageSpec


@dataclass(frozen=True, slots=True)
class FactoryStagePromptPolicy:
    """Hard prompt budgets for a single disposable factory stage."""

    initial_materialized_chars: int = 6_000
    continuation_tool_chars: int = 1_800
    assistant_content_chars: int = 1_200

    def normalized(self) -> "FactoryStagePromptPolicy":
        return FactoryStagePromptPolicy(
            initial_materialized_chars=max(
                1_000, min(int(self.initial_materialized_chars), 12_000)
            ),
            continuation_tool_chars=max(
                400, min(int(self.continuation_tool_chars), 4_000)
            ),
            assistant_content_chars=max(
                200, min(int(self.assistant_content_chars), 2_000)
            ),
        )


def _ref_id(item: dict[str, Any]) -> str:
    return str(item.get("ref") or item.get("id") or "").strip()


def _fingerprint(item: dict[str, Any]) -> str:
    raw = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _large_item_preview(item: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    """Preserve the useful text of one oversized context record inside a hard budget."""

    locator = _ref_id(item) or None
    preview: dict[str, Any] = {
        "ref": locator,
        "_operly_compacted": True,
    }
    for key in ("type", "kind", "source", "title", "name", "timestamp"):
        value = item.get(key)
        if value is not None and str(value).strip():
            preview[key] = str(value)[:240]

    text_value = ""
    for key in ("content", "text", "body", "message", "summary"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            text_value = value
            break
    if text_value:
        overhead = serialized_chars(preview) + 120
        available = max(200, max_chars - overhead)
        preview["content_preview"] = text_value[:available]
        if len(text_value) > available:
            preview["content_truncated"] = True

    preview["raw_chars"] = serialized_chars(item)
    # Extremely metadata-heavy records can still exceed the remaining allowance. In
    # that case retain the ref plus the bounded text preview and remove optional labels.
    while serialized_chars(preview) > max_chars:
        removable = next(
            (
                key
                for key in ("timestamp", "source", "kind", "type", "name", "title")
                if key in preview
            ),
            None,
        )
        if removable is not None:
            preview.pop(removable, None)
            continue
        content = str(preview.get("content_preview") or "")
        if len(content) <= 200:
            break
        preview["content_preview"] = content[: max(200, len(content) - 300)]
        preview["content_truncated"] = True
    return preview


class FactoryStagePromptPipeline:
    """Build and deterministically reset one worker's model-visible working set."""

    SYSTEM_MESSAGE = (
        "You are one disposable OPERLY factory worker. Complete only this bounded stage. "
        "The Factory owns the root objective, authorization, retries and completion truth. "
        "Use only supplied tools/context. Do not claim the whole user request is complete. "
        "Return concise stage output; durable results must be represented by tool evidence "
        "or artifact references. Prefer direct stage capabilities over capability discovery."
    )

    def __init__(
        self,
        *,
        stage: StageSpec,
        capsule: ContextCapsule,
        defect: Defect | None = None,
        policy: FactoryStagePromptPolicy | None = None,
    ) -> None:
        self.stage = stage
        self.capsule = capsule
        self.defect = defect
        self.policy = (policy or FactoryStagePromptPolicy()).normalized()

    def _bounded_materialized(self) -> list[dict[str, Any]]:
        budget = min(
            self.policy.initial_materialized_chars,
            max(1_000, int(self.capsule.max_context_chars)),
        )
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        used = 0
        for raw_item in self.capsule.materialized:
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            identity = _ref_id(item) or _fingerprint(item)
            if identity in seen:
                continue
            seen.add(identity)
            size = serialized_chars(item)
            remaining = budget - used
            if remaining <= 0:
                break
            if size <= remaining:
                output.append(item)
                used += size
                continue

            output.append(_large_item_preview(item, max_chars=remaining))
            break
        return output

    def _context_payload(self, *, include_materialized: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "context_refs": list(self.capsule.context_refs),
            "artifact_refs": list(self.capsule.artifact_refs),
            "facts": {key: value for key, value in self.capsule.facts},
            "capability_ids": list(self.capsule.capability_ids),
        }
        if include_materialized:
            payload["materialized"] = self._bounded_materialized()
        else:
            payload["materialized"] = []
            payload["materialized_context_replay"] = "disabled"
            payload["retrieval_rule"] = (
                "Use supplied refs/direct tools for any additional data; do not reconstruct "
                "or request the prior workspace transcript."
            )
        return payload

    def _payload(self, *, include_materialized: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": self.stage.as_dict(),
            "context_capsule": self._context_payload(
                include_materialized=include_materialized
            ),
            "worker_contract": {
                "do_only_stage": True,
                "do_not_replay_prior_worker_history": True,
                "return_artifact_or_evidence_handles": True,
                "materialized_context_is_single_use": True,
                "prefer_direct_capabilities": True,
            },
        }
        if self.defect is not None:
            payload["repair_defect"] = self.defect.as_dict()
            payload["repair_instruction"] = (
                "Use the defect evidence to choose a materially different repair when the "
                "previous strategy failed."
            )
        return payload

    def initial_messages(self) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": self.SYSTEM_MESSAGE},
            {
                "role": "user",
                "content": json.dumps(
                    self._payload(include_materialized=True),
                    ensure_ascii=False,
                    default=str,
                ),
            },
        ]

    def continuation_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Keep only the current tool protocol turn plus a reference-first stage envelope.

        Provider APIs generally require the assistant tool-call message immediately before
        matching tool results. We retain that one protocol turn, but discard every older
        assistant/tool round and never include materialized workspace context again.
        """

        latest_assistant_index: int | None = None
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if str(message.get("role") or "") != "assistant":
                continue
            if isinstance(message.get("tool_calls"), list) and message.get("tool_calls"):
                latest_assistant_index = index
                break

        reduced: list[dict[str, Any]] = [
            {"role": "system", "content": self.SYSTEM_MESSAGE},
            {
                "role": "user",
                "content": json.dumps(
                    self._payload(include_materialized=False),
                    ensure_ascii=False,
                    default=str,
                ),
            },
        ]
        if latest_assistant_index is None:
            return reduced

        tool_messages: list[dict[str, Any]] = []
        returned_call_ids: set[str] = set()
        for message in messages[latest_assistant_index + 1 :]:
            if str(message.get("role") or "") != "tool":
                continue
            tool_message = dict(message)
            tool_call_id = str(tool_message.get("tool_call_id") or "").strip()
            if tool_call_id:
                returned_call_ids.add(tool_call_id)
            tool_message["content"] = compact_tool_content(
                str(tool_message.get("content") or ""),
                max_chars=self.policy.continuation_tool_chars,
            )
            tool_messages.append(tool_message)

        assistant = dict(messages[latest_assistant_index])
        calls = list(assistant.get("tool_calls") or [])
        if returned_call_ids:
            assistant["tool_calls"] = [
                call
                for call in calls
                if isinstance(call, dict)
                and str(call.get("id") or "").strip() in returned_call_ids
            ]
        content = str(assistant.get("content") or "")
        if len(content) > self.policy.assistant_content_chars:
            assistant["content"] = (
                content[: self.policy.assistant_content_chars] + "… [bounded]"
            )
        reduced.append(assistant)

        allowed_call_ids = {
            str(call.get("id") or "").strip()
            for call in (assistant.get("tool_calls") or [])
            if isinstance(call, dict) and str(call.get("id") or "").strip()
        }
        for tool_message in tool_messages:
            tool_call_id = str(tool_message.get("tool_call_id") or "").strip()
            if allowed_call_ids and tool_call_id and tool_call_id not in allowed_call_ids:
                continue
            reduced.append(tool_message)
        return reduced
