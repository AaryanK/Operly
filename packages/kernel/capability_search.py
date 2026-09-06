from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol, Sequence

from packages.kernel.contracts import CapabilityRisk, CapabilitySpec


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _stem(token: str) -> str:
    """Apply small morphological normalization without domain dictionaries."""

    token = token.lower()
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith(("sses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokens(value: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            _stem(match.group(0))
            for match in _TOKEN_RE.finditer(str(value or "").lower())
            if len(match.group(0)) > 1
        )
    )


def _namespace_keys(capability_id: str) -> tuple[str, ...]:
    """Return bounded hierarchical capability-family prefixes, most-specific first.

    Capability IDs already encode useful provider/resource hierarchy. For example,
    ``google.gmail.search`` and ``google.gmail.read_message`` share ``google.gmail``.
    Expanding a strong seed to its local family lets discovery surface sibling actions
    without hard-coding domain synonyms. One-segment roots such as ``workspace`` are
    deliberately excluded because they can become enormous catalogs.
    """

    parts = [part for part in str(capability_id or "").split(".") if part]
    if len(parts) < 3:
        return ()
    return tuple(
        ".".join(parts[:depth])
        for depth in range(len(parts) - 1, 1, -1)
    )


# This is an operation ontology, not a capability/domain synonym table. It preserves
# distinctions such as SEARCH vs LIST vs READ while allowing natural verb variants to
# share one action facet. New capabilities inherit these facets from their own metadata;
# they do not require code changes here for every resource/domain they introduce.
_ACTION_ALIASES = {
    "search": "search",
    "find": "search",
    "lookup": "search",
    "query": "search",
    "discover": "search",
    "list": "list",
    "enumerate": "list",
    "browse": "list",
    "read": "read",
    "get": "read",
    "inspect": "read",
    "show": "read",
    "view": "read",
    "check": "read",
    "status": "read",
    "snapshot": "read",
    "preview": "read",
    "create": "create",
    "add": "create",
    "book": "create",
    "record": "create",
    "draft": "draft",
    "send": "send",
    "reply": "send",
    "forward": "send",
    "update": "update",
    "edit": "update",
    "modify": "update",
    "move": "update",
    "set": "update",
    "mark": "update",
    "delete": "delete",
    "remove": "delete",
    "archive": "delete",
    "run": "execute",
    "execute": "execute",
    "start": "execute",
    "retry": "retry",
    "cancel": "cancel",
    "stop": "cancel",
    "wait": "wait",
    "watch": "wait",
    "monitor": "wait",
}

_READ_ACTIONS = frozenset({"search", "list", "read"})
_MUTATION_ACTIONS = frozenset(
    {"create", "draft", "send", "update", "delete", "execute", "retry", "cancel"}
)


def action_facets(value: str) -> frozenset[str]:
    return frozenset(
        facet
        for token in tokens(value)
        if (facet := _ACTION_ALIASES.get(token)) is not None
    )


@dataclass(frozen=True, slots=True)
class CapabilitySearchQuery:
    raw: str
    objective: str
    resource_terms: frozenset[str]
    operation_terms: frozenset[str]
    objective_terms: frozenset[str]
    action_facets: frozenset[str]
    wants_retrieval: bool
    wants_mutation: bool

    @classmethod
    def parse(cls, value: str) -> "CapabilitySearchQuery":
        raw = str(value or "").strip().lower()
        parts = [part.strip() for part in raw.split("|") if part.strip()]
        objective = parts[0] if parts else raw
        resource_terms: set[str] = set()
        operation_terms: set[str] = set()
        for part in parts[1:]:
            lowered = part.lower()
            if lowered.startswith("resources "):
                resource_terms.update(tokens(part[len("resources ") :]))
            elif lowered.startswith("operations "):
                operation_terms.update(tokens(part[len("operations ") :]))

        objective_terms = frozenset(tokens(objective))
        all_actions = action_facets(objective)
        wants_retrieval = bool(operation_terms & {"retrieve", "read"})
        wants_mutation = bool(
            operation_terms & {"act", "create", "update", "delete", "send", "execute"}
        )
        return cls(
            raw=raw,
            objective=objective,
            resource_terms=frozenset(resource_terms),
            operation_terms=frozenset(operation_terms),
            objective_terms=objective_terms,
            action_facets=all_actions,
            wants_retrieval=wants_retrieval,
            wants_mutation=wants_mutation,
        )


@dataclass(frozen=True, slots=True)
class IndexedCapability:
    capability_id: str
    identity_terms: frozenset[str]
    description_terms: frozenset[str]
    schema_terms: frozenset[str]
    all_terms: frozenset[str]
    action_facets: frozenset[str]
    searchable_text: str


class SemanticCapabilityCandidateProvider(Protocol):
    """Optional ANN/vector candidate source for very large capability catalogs.

    The Kernel can later back this with pgvector, a local ANN index, or an external
    retrieval service without changing capability authorization or deterministic
    reranking. Provider results are only candidate IDs; the registry still applies
    trusted scope/surface/permission filtering before any candidate can be returned.
    """

    def candidate_ids(self, query: str, *, limit: int) -> Sequence[str]:
        ...


class CapabilitySearchIndex:
    """Incremental sparse index used for bounded candidate generation.

    Search never needs to score every registered capability. Query terms hit inverted
    postings first, strong seeds can expand into a bounded local capability family, and
    only that candidate pool is reranked. This keeps the in-process path useful for
    thousands of capabilities while leaving a clean seam for ANN/vector generation when
    catalogs become much larger.
    """

    def __init__(self) -> None:
        self._documents: dict[str, IndexedCapability] = {}
        self._postings: dict[str, set[str]] = defaultdict(set)
        self._action_postings: dict[str, set[str]] = defaultdict(set)
        self._namespace_postings: dict[str, set[str]] = defaultdict(set)

    def register(self, spec: CapabilitySpec) -> None:
        identity_text = " ".join(
            (spec.id, spec.display_name, *spec.aliases, *sorted(spec.tags))
        ).lower()
        description_text = str(spec.description or "").lower()
        schema_text = " ".join(
            (
                " ".join(map(str, spec.input_schema.keys())),
                " ".join(map(str, spec.output_schema.keys())),
                " ".join(spec.permissions),
                " ".join(spec.emits),
                spec.resource_scope,
            )
        ).lower()
        identity_terms = frozenset(tokens(identity_text))
        description_terms = frozenset(tokens(description_text))
        schema_terms = frozenset(tokens(schema_text))
        all_terms = identity_terms | description_terms | schema_terms
        actions = action_facets(f"{identity_text} {description_text}")
        document = IndexedCapability(
            capability_id=spec.id,
            identity_terms=identity_terms,
            description_terms=description_terms,
            schema_terms=schema_terms,
            all_terms=all_terms,
            action_facets=actions,
            searchable_text=f"{identity_text} {description_text}",
        )
        self._documents[spec.id] = document
        for term in all_terms:
            self._postings[term].add(spec.id)
        for action in actions:
            self._action_postings[action].add(spec.id)
        for namespace in _namespace_keys(spec.id):
            self._namespace_postings[namespace].add(spec.id)

    def document(self, capability_id: str) -> IndexedCapability:
        return self._documents[capability_id]

    def document_frequency(self, term: str) -> int:
        return len(self._postings.get(term, ()))

    @property
    def size(self) -> int:
        return len(self._documents)

    def candidate_ids(
        self,
        query: CapabilitySearchQuery,
        *,
        limit: int,
    ) -> tuple[str, ...]:
        if not query.raw:
            return ()

        budget = max(128, min(4096, max(1, limit) * 64))
        terms = set(query.objective_terms) | set(query.resource_terms)
        # Rare terms carry far more routing information than generic words like
        # "workspace" or "show". Consume rare postings first and stop at a bounded pool.
        ranked_terms = sorted(
            (term for term in terms if term in self._postings),
            key=lambda term: (self.document_frequency(term), term),
        )
        candidates: set[str] = set()
        for term in ranked_terms:
            postings = self._postings[term]
            remaining = budget - len(candidates)
            if remaining <= 0:
                break
            if len(postings) <= remaining:
                candidates.update(postings)
            else:
                candidates.update(sorted(postings)[:remaining])

        # Specific action facets are cheap high-value postings. They are merged rather
        # than treated as a hard filter because a capability may use unconventional
        # naming while still being semantically relevant through its description.
        for action in sorted(query.action_facets):
            postings = self._action_postings.get(action, ())
            remaining = budget - len(candidates)
            if remaining <= 0:
                break
            candidates.update(list(sorted(postings))[:remaining])

        return tuple(candidates)

    def related_candidate_ids(
        self,
        seed_ids: Sequence[str],
        *,
        limit: int,
    ) -> tuple[str, ...]:
        """Expand seed hits into their nearest capability family, with a hard budget."""

        budget = max(16, min(1024, int(limit)))
        related: list[str] = []
        seen = set(seed_ids)
        for seed_id in seed_ids:
            for namespace in _namespace_keys(seed_id):
                siblings = self._namespace_postings.get(namespace, ())
                # Skip pathological broad families and fall through to a more specific
                # seed/ANN path instead of flooding the candidate pool.
                if len(siblings) > budget * 4:
                    continue
                for sibling_id in sorted(siblings):
                    if sibling_id in seen:
                        continue
                    seen.add(sibling_id)
                    related.append(sibling_id)
                    if len(related) >= budget:
                        return tuple(related)
                # The most-specific useful namespace is enough for this seed. This avoids
                # walking upward into broad provider families such as workspace.finance.
                if siblings:
                    break
        return tuple(related)

    def score(self, query: CapabilitySearchQuery, spec: CapabilitySpec) -> float:
        doc = self._documents[spec.id]
        total_docs = max(1, self.size)

        def idf(term: str) -> float:
            return math.log(
                (total_docs + 1.0) / (self.document_frequency(term) + 1.0)
            ) + 1.0

        score = 0.0
        if query.raw == spec.id:
            return 1_000_000.0
        if query.objective and query.objective in doc.searchable_text:
            score += 45.0

        for term in query.objective_terms:
            weight = idf(term)
            if term in doc.identity_terms:
                score += 13.0 * weight
            elif term in doc.description_terms:
                score += 7.0 * weight
            elif term in doc.schema_terms:
                score += 2.0 * weight

        for term in query.resource_terms:
            weight = idf(term)
            if term in doc.identity_terms:
                score += 20.0 * weight
            elif term in doc.description_terms:
                score += 13.0 * weight
            elif term in doc.schema_terms:
                score += 5.0 * weight

        if query.action_facets:
            overlap = query.action_facets & doc.action_facets
            if overlap:
                score += 75.0 + 20.0 * len(overlap)
            else:
                # Preserve the broader read-family relationship without equating
                # SEARCH/LIST/READ. A search request may still consider a list/read tool,
                # but exact action semantics should win decisively.
                query_read = bool(query.action_facets & _READ_ACTIONS)
                doc_read = bool(doc.action_facets & _READ_ACTIONS)
                query_write = bool(query.action_facets & _MUTATION_ACTIONS)
                doc_write = bool(doc.action_facets & _MUTATION_ACTIONS)
                if query_read and doc_read:
                    score += 18.0
                if query_write and doc_write:
                    score += 12.0

        if query.wants_retrieval:
            score += 24.0 if spec.risk is CapabilityRisk.READ_ONLY else -20.0
        if query.wants_mutation:
            score += 16.0 if spec.risk is not CapabilityRisk.READ_ONLY else -8.0

        return score


__all__ = [
    "CapabilitySearchIndex",
    "CapabilitySearchQuery",
    "SemanticCapabilityCandidateProvider",
    "action_facets",
    "tokens",
]
