"""Stage-scoped context compilation for the Operly factory control plane.

The injector knows the authorized context universe; workers do not. It searches by
stage intent, returns references first, materializes only a bounded subset, and never
widens authority. Existing ContextBroker/ContextService remain the storage/retrieval
primitives; this module is the orchestration seam above them.
"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

from .contracts import ContextCapsule, StageSpec


ContextSearch = Callable[
    [str, int],
    Awaitable[list[dict[str, Any]]] | list[dict[str, Any]],
]
ContextMaterialize = Callable[
    [list[str]],
    Awaitable[list[dict[str, Any]]] | list[dict[str, Any]],
]
CapabilityResolver = Callable[
    [Iterable[str]],
    Awaitable[list[str]] | list[str],
]


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


def _ref_id(item: dict[str, Any]) -> str:
    return str(item.get("ref") or item.get("id") or "").strip()


def _estimated_tokens(item: dict[str, Any]) -> int:
    try:
        return max(1, int(item.get("estimated_tokens") or 0))
    except (TypeError, ValueError):
        return max(1, len(json.dumps(item, ensure_ascii=False, default=str)) // 4)


def _serialized_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


@dataclass(frozen=True, slots=True)
class ContextInjectionPolicy:
    max_searches: int = 6
    refs_per_intent: int = 5
    max_refs: int = 16
    max_materialized_refs: int = 8
    max_materialized_chars: int = 18_000

    def normalized(self) -> "ContextInjectionPolicy":
        return ContextInjectionPolicy(
            max_searches=max(0, min(int(self.max_searches), 20)),
            refs_per_intent=max(1, min(int(self.refs_per_intent), 20)),
            max_refs=max(1, min(int(self.max_refs), 50)),
            max_materialized_refs=max(0, min(int(self.max_materialized_refs), 20)),
            max_materialized_chars=max(1_000, min(int(self.max_materialized_chars), 100_000)),
        )


class StageContextInjector:
    """Compile the minimum authorized context/capability capsule for one stage."""

    def __init__(
        self,
        *,
        search: ContextSearch | None = None,
        materialize: ContextMaterialize | None = None,
        resolve_capabilities: CapabilityResolver | None = None,
        policy: ContextInjectionPolicy | None = None,
    ) -> None:
        self.search = search
        self.materialize = materialize
        self.resolve_capabilities = resolve_capabilities
        self.policy = (policy or ContextInjectionPolicy()).normalized()

    async def _search_refs(self, intents: Iterable[str]) -> list[dict[str, Any]]:
        if self.search is None:
            return []
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        for intent in list(intents)[: self.policy.max_searches]:
            clean = " ".join(str(intent or "").split()).strip()
            if not clean:
                continue
            rows = list(await _resolve(self.search(clean, self.policy.refs_per_intent)) or [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ref = _ref_id(row)
                if not ref or ref in seen:
                    continue
                seen.add(ref)
                output.append(dict(row))
                if len(output) >= self.policy.max_refs:
                    return output
        return output

    async def _materialize_bounded(
        self,
        refs: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        if self.materialize is None or self.policy.max_materialized_refs <= 0:
            return ()
        # Explicit/inherited refs receive a high score before this function is called.
        # The materializer must still reauthorize every locator; refs are never bearer
        # tokens. Smaller high-ranked references are preferred inside the hard budget.
        ordered = sorted(
            refs,
            key=lambda item: (
                -float(item.get("score") or 0.0),
                _estimated_tokens(item),
                _ref_id(item),
            ),
        )
        candidate_ids = [
            _ref_id(item)
            for item in ordered[: self.policy.max_materialized_refs]
            if _ref_id(item)
        ]
        rows = list(await _resolve(self.materialize(candidate_ids)) or [])
        by_ref = {
            _ref_id(row): dict(row)
            for row in rows
            if isinstance(row, dict) and _ref_id(row)
        }
        output: list[dict[str, Any]] = []
        used_chars = 0
        # Preserve priority/request order even if the backing store reorders rows.
        for ref in candidate_ids:
            item = by_ref.get(ref)
            if item is None:
                continue
            size = _serialized_chars(item)
            remaining = self.policy.max_materialized_chars - used_chars
            if remaining <= 0:
                break
            if size > remaining:
                # Never cut opaque structured data into invalid JSON. Keep only the
                # ref in the capsule when materialization would break the budget.
                continue
            output.append(item)
            used_chars += size
        return tuple(output)

    async def build(
        self,
        stage: StageSpec,
        *,
        inherited_context_refs: Iterable[str] = (),
        artifact_refs: Iterable[str] = (),
        facts: dict[str, Any] | None = None,
    ) -> ContextCapsule:
        discovered = await self._search_refs(stage.context_intents)
        refs: list[str] = []
        seen: set[str] = set()
        inherited = [
            str(value or "").strip()
            for value in inherited_context_refs
            if str(value or "").strip()
        ]
        for value in [*inherited, *(_ref_id(item) for item in discovered)]:
            clean = str(value or "").strip()
            if clean and clean not in seen:
                seen.add(clean)
                refs.append(clean)
            if len(refs) >= self.policy.max_refs:
                break

        capability_ids: list[str] = []
        if self.resolve_capabilities is not None and stage.capability_intents:
            resolved = await _resolve(self.resolve_capabilities(stage.capability_intents))
            capability_ids = [str(item) for item in (resolved or []) if str(item).strip()]

        # Root/application-selected refs are materialization candidates too. They are
        # not blindly injected: the authorized materializer rechecks each ref, and the
        # same hard count/character budgets apply. This lets retained attachments or
        # other exact referents enter only the stage capsule that needs them without
        # replaying a conversation transcript.
        candidate_by_ref = {
            _ref_id(item): dict(item)
            for item in discovered
            if _ref_id(item) in set(refs)
        }
        for ref in refs:
            if ref not in candidate_by_ref:
                candidate_by_ref[ref] = {
                    "ref": ref,
                    "score": 2.0,
                    "estimated_tokens": 1,
                }
        materialized = await self._materialize_bounded(
            [candidate_by_ref[ref] for ref in refs if ref in candidate_by_ref]
        )

        bounded_facts = tuple(
            (str(key)[:120], value)
            for key, value in list((facts or {}).items())[:24]
        )
        artifacts = tuple(
            dict.fromkeys(str(item) for item in artifact_refs if str(item).strip())
        )[:32]
        return ContextCapsule(
            stage_id=stage.id,
            objective=stage.objective,
            context_refs=tuple(refs),
            artifact_refs=artifacts,
            facts=bounded_facts,
            materialized=materialized,
            capability_ids=tuple(dict.fromkeys(capability_ids))[:24],
            max_context_chars=self.policy.max_materialized_chars,
        )
