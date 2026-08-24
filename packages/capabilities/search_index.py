"""Metadata-only hybrid semantic retrieval for capability discovery.

The index intentionally knows nothing about principals or permissions. Callers pass
an already-authorized and surface-visible candidate set. Production semantic scores
come from the shared local ONNX embedding backend; lexical/exact signals remain as a
precision boost and deterministic degraded-mode fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Sequence

from packages.retrieval.semantic import SemanticDocument, SemanticTextIndex


_WORD_RE = re.compile(r"[a-z0-9_.:-]+")

# These are lexical boosts only. They are not the semantic engine; the production
# semantic signal comes from the embedding backend in SemanticTextIndex.
_LEXICAL_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "email": ("mail", "message", "gmail", "inbox"),
    "mail": ("email", "message", "gmail", "inbox"),
    "message": ("email", "mail", "send", "communication"),
    "schedule": ("calendar", "event", "meeting", "availability"),
    "meeting": ("calendar", "event", "schedule", "availability"),
    "customer": ("crm", "contact", "lead", "client"),
    "client": ("customer", "crm", "contact", "lead"),
    "sales": ("crm", "lead", "pipeline", "deal", "customer"),
    "website": ("site", "web", "page", "seo"),
    "seo": ("website", "web", "search", "content"),
    "file": ("artifact", "document", "attachment", "source"),
    "document": ("file", "artifact", "attachment", "report"),
    "code": ("coding", "source", "software", "project"),
    "research": ("search", "web", "investigate", "evidence"),
    "remember": ("memory", "context", "preference", "fact"),
    "memory": ("context", "remember", "preference", "fact"),
}


def _words(value: str) -> list[str]:
    return _WORD_RE.findall(str(value or "").lower())


def _expanded_words(value: str) -> list[str]:
    words = _words(value)
    expanded = list(words)
    for word in words:
        expanded.extend(_LEXICAL_EXPANSIONS.get(word, ()))
    return expanded


@dataclass(frozen=True, slots=True)
class CapabilitySearchHit:
    capability_id: str
    score: float
    semantic_score: float
    lexical_score: float


class CapabilitySearchIndex:
    """Exact hybrid ranking over CapabilityDefinition.discovery_document()."""

    def __init__(self, *, semantic_index: SemanticTextIndex | None = None) -> None:
        self.semantic_index = semantic_index or SemanticTextIndex()

    @property
    def backend_name(self) -> str:
        return self.semantic_index.backend_name

    @property
    def degraded_reason(self) -> str | None:
        return self.semantic_index.degraded_reason

    @staticmethod
    def _lexical_score(definition, query: str) -> float:
        normalized = str(query or "").strip().lower()
        query_words = set(_expanded_words(normalized))
        document_words = set(_expanded_words(definition.discovery_document()))
        overlap = len(query_words & document_words)
        score = float(overlap)
        capability_id = str(definition.id or "").lower()
        name = str(definition.name or "").lower()
        display_name = str(definition.display_name or definition.name or "").lower()
        category = str(definition.category or "").lower()
        tags = {str(tag).lower() for tag in definition.tags}
        operations = " ".join(str(item).lower() for item in definition.semantic_operations)
        if normalized:
            if normalized in {capability_id, name}:
                score += 12.0
            elif normalized in capability_id or normalized in display_name:
                score += 5.0
            if normalized in operations:
                score += 4.0
        if category and category in query_words:
            score += 2.0
        score += 1.5 * len(tags & query_words)
        return score

    def search(
        self,
        definitions: Sequence,
        query: str,
        *,
        limit: int = 8,
        categories: Iterable[str] = (),
        tags: Iterable[str] = (),
    ) -> list[CapabilitySearchHit]:
        wanted_categories = {
            str(item).strip().lower() for item in categories if str(item).strip()
        }
        wanted_tags = {str(item).strip().lower() for item in tags if str(item).strip()}
        eligible = []
        for definition in definitions:
            if wanted_categories and str(definition.category or "").lower() not in wanted_categories:
                continue
            definition_tags = {str(item).lower() for item in definition.tags}
            if wanted_tags and not wanted_tags.issubset(definition_tags):
                continue
            eligible.append(definition)

        semantic_scores: dict[str, float] = {}
        clean_query = str(query or "").strip()
        if eligible and clean_query:
            documents = [
                SemanticDocument(
                    key=str(definition.id),
                    text=str(definition.discovery_document() or ""),
                )
                for definition in eligible
            ]
            semantic_scores = {
                match.key: match.score
                for match in self.semantic_index.rank(
                    documents,
                    clean_query,
                    limit=len(documents),
                )
            }

        ranked: list[CapabilitySearchHit] = []
        for definition in eligible:
            capability_id = str(definition.id)
            semantic = semantic_scores.get(capability_id, 0.0)
            lexical = self._lexical_score(definition, clean_query)
            score = semantic * 10.0 + lexical
            if clean_query and score <= 0.0:
                continue
            ranked.append(
                CapabilitySearchHit(
                    capability_id=capability_id,
                    score=round(score, 6),
                    semantic_score=round(semantic, 6),
                    lexical_score=round(lexical, 6),
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.capability_id))
        return ranked[: max(1, min(int(limit), 20))]
