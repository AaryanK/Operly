"""Operation-aware capability intent resolution for the Factory control plane."""
from __future__ import annotations

from typing import Iterable

from .safe_factory import (
    SafeFactoryCapabilityIntentResolver,
    _capability_matches_intent,
    _tokens,
)


_OPERATION_GROUPS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"create", "add"}), frozenset({"create", "add"})),
    (frozenset({"draft"}), frozenset({"draft"})),
    (frozenset({"send"}), frozenset({"send"})),
    (frozenset({"delete", "remove"}), frozenset({"delete", "remove"})),
    (frozenset({"cancel"}), frozenset({"cancel"})),
    (frozenset({"update", "edit", "modify"}), frozenset({"update", "edit", "modify", "set"})),
    (frozenset({"complete", "close"}), frozenset({"complete", "close"})),
    (frozenset({"publish"}), frozenset({"publish"})),
    (frozenset({"archive"}), frozenset({"archive"})),
    (frozenset({"move"}), frozenset({"move"})),
    (frozenset({"rename"}), frozenset({"rename"})),
    (frozenset({"adjust"}), frozenset({"adjust"})),
    (frozenset({"search", "find", "query", "lookup"}), frozenset({"search", "find", "query", "lookup"})),
    (frozenset({"list"}), frozenset({"list"})),
)
_READ_FALLBACK_INTENT = frozenset(
    {"read", "get", "retrieve", "fetch", "check", "inspect", "view", "analyze", "analyse"}
)
_READ_CAPABILITY_OPERATIONS = frozenset(
    {"read", "get", "retrieve", "fetch", "check", "inspect", "view", "search", "find", "query", "lookup", "list"}
)


def _operation_matches(capability_id: str, intent: str) -> bool:
    """Require an intent's explicit verb to be represented by the capability ID.

    Provider family matching prevents cross-domain substitutions.  This second gate
    prevents an operation inside the right family from standing in for another one,
    e.g. ``task.list`` satisfying "Create tasks" or ``gmail.search`` satisfying
    "Send email".
    """

    intent_tokens = _tokens(intent)
    capability_tokens = _tokens(capability_id)
    for intent_group, capability_group in _OPERATION_GROUPS:
        if intent_tokens & intent_group:
            return bool(capability_tokens & capability_group)
    if intent_tokens & _READ_FALLBACK_INTENT:
        return bool(capability_tokens & _READ_CAPABILITY_OPERATIONS)
    # Generic intents with no explicit operation retain the safe family-ranked
    # behavior; validators still bind completion to real capability evidence.
    return True


class StrictFactoryCapabilityIntentResolver(SafeFactoryCapabilityIntentResolver):
    """Resolve only authorized capabilities matching both domain and operation."""

    async def __call__(self, intents: Iterable[str]) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for intent in list(intents)[:8]:
            clean = " ".join(str(intent or "").split()).strip()
            if not clean:
                continue
            rows = self.registry.search(
                self.scope_id,
                clean,
                authority=self.authority,
                limit=max(self.max_per_intent * 4, 8),
            )
            added = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                capability_id = str(row.get("id") or "").strip()
                if (
                    not capability_id
                    or capability_id in seen
                    or not _capability_matches_intent(capability_id, clean)
                    or not _operation_matches(capability_id, clean)
                    or not self._allowed(capability_id)
                ):
                    continue
                seen.add(capability_id)
                selected.append(capability_id)
                added += 1
                if added >= self.max_per_intent or len(selected) >= self.max_total:
                    break
            if len(selected) >= self.max_total:
                break
        if selected and self.session_view is not None:
            self.session_view.expose(selected)
        return selected
