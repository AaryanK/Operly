from __future__ import annotations

import re
from collections.abc import Iterable

from packages.kernel.contracts import CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.security.surfaces import capability_surface_allowed


class CapabilityRegistryError(RuntimeError):
    pass


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", str(value or "").lower())
        if len(token) > 1
    }


class CapabilityRegistry:
    """Single source of truth for model/API visible capability contracts."""

    def __init__(self, specs: Iterable[CapabilitySpec] = ()) -> None:
        self._specs: dict[str, CapabilitySpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: CapabilitySpec) -> None:
        capability_id = spec.id.strip().lower()
        if not capability_id or capability_id != spec.id:
            raise CapabilityRegistryError("Capability IDs must be normalized lowercase names")
        if capability_id in self._specs:
            raise CapabilityRegistryError(f"Duplicate capability: {capability_id}")
        self._specs[capability_id] = spec

    def get(self, capability_id: str) -> CapabilitySpec:
        key = str(capability_id or "").strip().lower()
        try:
            return self._specs[key]
        except KeyError as error:
            raise CapabilityRegistryError(f"Unknown capability: {key or '<empty>'}") from error

    def all(self) -> tuple[CapabilitySpec, ...]:
        return tuple(self._specs[key] for key in sorted(self._specs))

    def visible(self, context: ExecutionContext) -> tuple[CapabilitySpec, ...]:
        scope = context.scope_kind.value
        return tuple(
            spec
            for spec in self.all()
            if scope in spec.scopes
            and capability_surface_allowed(spec.id, context.surface)
        )

    def effective(self, context: ExecutionContext) -> tuple[CapabilitySpec, ...]:
        return tuple(
            spec
            for spec in self.visible(context)
            if all(context.can(permission) for permission in spec.permissions)
        )

    def search(
        self,
        query: str,
        *,
        context: ExecutionContext,
        effective_only: bool = False,
        limit: int = 10,
    ) -> tuple[CapabilitySpec, ...]:
        candidates = self.effective(context) if effective_only else self.visible(context)
        query_text = str(query or "").strip().lower()
        if not query_text:
            return candidates[: max(1, min(limit, 50))]
        query_tokens = _tokens(query_text)
        ranked: list[tuple[int, str, CapabilitySpec]] = []
        for spec in candidates:
            haystacks = [
                spec.id,
                spec.display_name,
                spec.description,
                *spec.aliases,
                *spec.tags,
            ]
            joined = " ".join(haystacks).lower()
            score = 0
            if query_text == spec.id:
                score += 100
            if query_text in joined:
                score += 20
            spec_tokens = _tokens(joined)
            score += 5 * len(query_tokens & spec_tokens)
            if score:
                ranked.append((score, spec.id, spec))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        return tuple(row[2] for row in ranked[: max(1, min(limit, 50))])
