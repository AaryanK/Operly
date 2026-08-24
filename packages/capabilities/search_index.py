"""Fast metadata-only semantic retrieval for capability discovery.

The index intentionally knows nothing about principals or permissions. Callers must
pass an already-authorized/visible candidate set. This keeps semantic ranking out of
the authority path while avoiding thousands of schemas in model prompts.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2b
from math import sqrt
import re
from typing import Iterable, Sequence


_WORD_RE = re.compile(r"[a-z0-9_.:-]+")

_SEMANTIC_EXPANSIONS: dict[str, tuple[str, ...]] = {
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
        expanded.extend(_SEMANTIC_EXPANSIONS.get(word, ()))
    return expanded


def _feature_key(feature: str, dimensions: int) -> int:
    digest = blake2b(feature.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dimensions


def _vector(value: str, *, dimensions: int = 384) -> dict[int, float]:
    words = _expanded_words(value)
    features: dict[int, float] = {}
    for word in words:
        key = _feature_key(f"w:{word}", dimensions)
        features[key] = features.get(key, 0.0) + 1.0
    for left, right in zip(words, words[1:]):
        key = _feature_key(f"b:{left}:{right}", dimensions)
        features[key] = features.get(key, 0.0) + 0.65
    compact = " ".join(words)
    for size in (3, 4, 5):
        if len(compact) < size:
            continue
        for index in range(0, len(compact) - size + 1):
            gram = compact[index : index + size]
            key = _feature_key(f"c{size}:{gram}", dimensions)
            features[key] = features.get(key, 0.0) + 0.08
    norm = sqrt(sum(weight * weight for weight in features.values()))
    if norm:
        return {key: weight / norm for key, weight in features.items()}
    return {}


def _cosine(left: dict[int, float], right: dict[int, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(weight * right.get(key, 0.0) for key, weight in left.items())


@dataclass(frozen=True, slots=True)
class CapabilitySearchHit:
    capability_id: str
    score: float
    semantic_score: float
    lexical_score: float


class CapabilitySearchIndex:
    """In-process hybrid index over CapabilityDefinition.discovery_document()."""

    def __init__(self, *, dimensions: int = 384) -> None:
        self.dimensions = max(64, int(dimensions))
        self._documents: dict[str, str] = {}
        self._vectors: dict[str, dict[int, float]] = {}

    def _ensure(self, definition) -> None:
        document = str(definition.discovery_document() or "").strip().lower()
        capability_id = str(definition.id)
        if self._documents.get(capability_id) == document:
            return
        self._documents[capability_id] = document
        self._vectors[capability_id] = _vector(document, dimensions=self.dimensions)

    @staticmethod
    def _lexical_score(definition, query: str) -> float:
        normalized = str(query or "").strip().lower()
        query_words = set(_expanded_words(normalized))
        document_words = set(_expanded_words(definition.discovery_document()))
        overlap = len(query_words & document_words)
        score = float(overlap)
        capability_id = str(definition.id or "").lower()
        display_name = str(definition.display_name or definition.name or "").lower()
        category = str(definition.category or "").lower()
        tags = {str(tag).lower() for tag in definition.tags}
        operations = " ".join(str(item).lower() for item in definition.semantic_operations)
        if normalized:
            if normalized == capability_id or normalized == str(definition.name or "").lower():
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
        query_vector = _vector(query, dimensions=self.dimensions)
        ranked: list[CapabilitySearchHit] = []
        for definition in definitions:
            if wanted_categories and str(definition.category or "").lower() not in wanted_categories:
                continue
            definition_tags = {str(item).lower() for item in definition.tags}
            if wanted_tags and not wanted_tags.issubset(definition_tags):
                continue
            self._ensure(definition)
            semantic = _cosine(query_vector, self._vectors.get(str(definition.id), {}))
            lexical = self._lexical_score(definition, query)
            score = semantic * 10.0 + lexical
            if str(query or "").strip() and score <= 0.0:
                continue
            ranked.append(
                CapabilitySearchHit(
                    capability_id=str(definition.id),
                    score=round(score, 6),
                    semantic_score=round(semantic, 6),
                    lexical_score=round(lexical, 6),
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.capability_id))
        return ranked[: max(1, min(int(limit), 20))]
