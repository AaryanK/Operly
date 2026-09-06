from __future__ import annotations

from collections.abc import Iterable

from packages.kernel.capability_search import (
    CapabilitySearchIndex,
    CapabilitySearchQuery,
    SemanticCapabilityCandidateProvider,
)
from packages.kernel.contracts import CapabilityRisk, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.security.surfaces import capability_surface_allowed


class CapabilityRegistryError(RuntimeError):
    pass


class CapabilityRegistry:
    """Single source of truth for model/API visible capability contracts.

    Capability search is deliberately two-stage:
      1. bounded sparse/semantic candidate generation plus local family expansion,
      2. trusted scope/surface/permission filtering followed by deterministic reranking.

    This prevents the model from seeing unauthorized contracts and avoids scoring every
    registered capability on each request as the catalog grows.
    """

    def __init__(
        self,
        specs: Iterable[CapabilitySpec] = (),
        *,
        semantic_candidate_provider: SemanticCapabilityCandidateProvider | None = None,
    ) -> None:
        self._specs: dict[str, CapabilitySpec] = {}
        self._search_index = CapabilitySearchIndex()
        self._semantic_candidate_provider = semantic_candidate_provider
        for spec in specs:
            self.register(spec)

    def set_semantic_candidate_provider(
        self,
        provider: SemanticCapabilityCandidateProvider | None,
    ) -> None:
        """Attach an optional vector/ANN candidate source without changing Kernel policy."""

        self._semantic_candidate_provider = provider

    def register(self, spec: CapabilitySpec) -> None:
        capability_id = spec.id.strip().lower()
        if not capability_id or capability_id != spec.id:
            raise CapabilityRegistryError("Capability IDs must be normalized lowercase names")
        if capability_id in self._specs:
            raise CapabilityRegistryError(f"Duplicate capability: {capability_id}")
        self._specs[capability_id] = spec
        self._search_index.register(spec)

    def get(self, capability_id: str) -> CapabilitySpec:
        key = str(capability_id or "").strip().lower()
        try:
            return self._specs[key]
        except KeyError as error:
            raise CapabilityRegistryError(f"Unknown capability: {key or '<empty>'}") from error

    def all(self) -> tuple[CapabilitySpec, ...]:
        return tuple(self._specs[key] for key in sorted(self._specs))

    @staticmethod
    def _surface_allowed(spec: CapabilitySpec, context: ExecutionContext) -> bool:
        return (
            context.scope_kind.value in spec.scopes
            and capability_surface_allowed(spec.id, context.surface)
        )

    @classmethod
    def _effective_allowed(cls, spec: CapabilitySpec, context: ExecutionContext) -> bool:
        return cls._surface_allowed(spec, context) and all(
            context.can(permission) for permission in spec.permissions
        )

    def visible(self, context: ExecutionContext) -> tuple[CapabilitySpec, ...]:
        return tuple(spec for spec in self.all() if self._surface_allowed(spec, context))

    def effective(self, context: ExecutionContext) -> tuple[CapabilitySpec, ...]:
        return tuple(
            spec
            for spec in self.visible(context)
            if all(context.can(permission) for permission in spec.permissions)
        )

    def _allowed(
        self,
        spec: CapabilitySpec,
        *,
        context: ExecutionContext,
        effective_only: bool,
    ) -> bool:
        if effective_only:
            return self._effective_allowed(spec, context)
        return self._surface_allowed(spec, context)

    def search(
        self,
        query: str,
        *,
        context: ExecutionContext,
        effective_only: bool = False,
        limit: int = 10,
    ) -> tuple[CapabilitySpec, ...]:
        bounded_limit = max(1, min(limit, 50))
        query_text = str(query or "").strip().lower()
        if not query_text:
            candidates = self.effective(context) if effective_only else self.visible(context)
            return candidates[:bounded_limit]

        # Exact IDs are still deterministic, but authorization/surface filtering remains
        # mandatory before the capability can be returned.
        exact = self._specs.get(query_text)
        if exact is not None and self._allowed(
            exact,
            context=context,
            effective_only=effective_only,
        ):
            return (exact,)

        parsed = CapabilitySearchQuery.parse(query_text)
        candidate_ids: list[str] = list(
            self._search_index.candidate_ids(parsed, limit=bounded_limit)
        )

        # A strong sparse hit can reveal a useful local capability family even when the
        # sibling uses different wording. Example: google.gmail.search should make
        # google.gmail.read_message eligible for reranking without teaching the registry
        # that "email" means "gmail". Expansion is indexed, namespace-derived and hard
        # bounded; one-segment roots are never expanded.
        family_limit = max(32, min(512, bounded_limit * 16))
        for capability_id in self._search_index.related_candidate_ids(
            candidate_ids,
            limit=family_limit,
        ):
            if capability_id not in candidate_ids:
                candidate_ids.append(capability_id)

        semantic_ids: set[str] = set()
        # Future large catalogs can add ANN/vector candidates here. Candidate IDs are
        # never trusted: unknown IDs are ignored and every known candidate is filtered by
        # the same trusted scope/surface/permission rules before reranking or exposure.
        if self._semantic_candidate_provider is not None:
            semantic_limit = max(64, min(2048, bounded_limit * 32))
            for capability_id in self._semantic_candidate_provider.candidate_ids(
                query_text,
                limit=semantic_limit,
            ):
                normalized = str(capability_id or "").strip().lower()
                if not normalized:
                    continue
                semantic_ids.add(normalized)
                if normalized not in candidate_ids:
                    candidate_ids.append(normalized)

        ranked: list[tuple[float, str, CapabilitySpec]] = []
        seen: set[str] = set()
        pure_retrieval = parsed.wants_retrieval and not parsed.wants_mutation
        for capability_id in candidate_ids:
            if capability_id in seen:
                continue
            seen.add(capability_id)
            spec = self._specs.get(capability_id)
            if spec is None or not self._allowed(
                spec,
                context=context,
                effective_only=effective_only,
            ):
                continue
            # A request classified as read-only should not expose mutating contracts to
            # the next-move model at all. Compound retrieve+act objectives are unaffected.
            # This is both better relevance and least-privilege discovery.
            if pure_retrieval and spec.risk is not CapabilityRisk.READ_ONLY:
                continue
            score = self._search_index.score(parsed, spec)
            # ANN/vector providers are candidate generators, not authorities or final
            # rankers. A semantically retrieved candidate with zero lexical score remains
            # eligible at a tiny floor so true vocabulary-gap results are not discarded.
            if capability_id in semantic_ids and score <= 0:
                score = 1.0
            if score > 0:
                ranked.append((score, spec.id, spec))

        ranked.sort(key=lambda row: (-row[0], row[1]))
        return tuple(row[2] for row in ranked[:bounded_limit])
