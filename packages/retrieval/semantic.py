"""Deterministic local retrieval primitives with no model downloads or ML runtime.

This module intentionally performs only lightweight, in-process hashing similarity.
It never imports FastEmbed, ONNX Runtime, Hugging Face Hub, or downloads model files.
Search callers still own authorization: this module only ranks documents supplied to it.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import blake2b
from math import sqrt
import re
from threading import RLock
from typing import Iterable, Protocol, Sequence


_TOKEN_RE = re.compile(r"[a-z0-9_.:-]+")


@dataclass(frozen=True, slots=True)
class SemanticDocument:
    key: str
    text: str


@dataclass(frozen=True, slots=True)
class SemanticMatch:
    key: str
    score: float


class EmbeddingBackend(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def degraded_reason(self) -> str | None: ...

    def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]: ...

    def embed_query(self, text: str) -> tuple[float, ...]: ...


def _normalized(values: Iterable[float]) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    norm = sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return tuple(value / norm for value in vector)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


class HashingEmbeddingBackend:
    """Deterministic, dependency-free, no-network text similarity backend."""

    def __init__(self, *, dimensions: int = 384, reason: str | None = None) -> None:
        self.dimensions = max(64, int(dimensions))
        self._reason = reason

    @property
    def name(self) -> str:
        return f"hashing:{self.dimensions}"

    @property
    def degraded_reason(self) -> str | None:
        return self._reason

    def _embed_one(self, text: str) -> tuple[float, ...]:
        words = _TOKEN_RE.findall(str(text or "").lower())
        values = [0.0] * self.dimensions
        for word in words:
            digest = blake2b(f"w:{word}".encode("utf-8"), digest_size=8).digest()
            values[int.from_bytes(digest, "big") % self.dimensions] += 1.0
        for left, right in zip(words, words[1:]):
            digest = blake2b(f"b:{left}:{right}".encode("utf-8"), digest_size=8).digest()
            values[int.from_bytes(digest, "big") % self.dimensions] += 0.45
        compact = " ".join(words)
        for size in (3, 4):
            for index in range(max(0, len(compact) - size + 1)):
                gram = compact[index : index + size]
                digest = blake2b(f"c:{gram}".encode("utf-8"), digest_size=8).digest()
                values[int.from_bytes(digest, "big") % self.dimensions] += 0.05
        return _normalized(values)

    def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [self._embed_one(text) for text in texts]

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._embed_one(text)


_BACKEND: EmbeddingBackend | None = None
_BACKEND_LOCK = RLock()


def embedding_backend() -> EmbeddingBackend:
    """Return the only supported retrieval backend: deterministic local hashing."""
    global _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND is None:
            _BACKEND = HashingEmbeddingBackend()
        return _BACKEND


class SemanticTextIndex:
    """Exact cosine search over lightweight deterministic hashed text features."""

    def __init__(
        self,
        *,
        backend: EmbeddingBackend | None = None,
        max_cached_documents: int = 20_000,
        max_cached_queries: int = 512,
    ) -> None:
        self.backend = backend or embedding_backend()
        self.max_cached_documents = max(128, int(max_cached_documents))
        self.max_cached_queries = max(32, int(max_cached_queries))
        self._documents: OrderedDict[str, tuple[str, tuple[float, ...]]] = OrderedDict()
        self._queries: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._lock = RLock()

    @property
    def backend_name(self) -> str:
        return self.backend.name

    @property
    def degraded_reason(self) -> str | None:
        return self.backend.degraded_reason

    @staticmethod
    def _fingerprint(text: str) -> str:
        return blake2b(str(text or "").encode("utf-8"), digest_size=12).hexdigest()

    @staticmethod
    def _cache_put(cache: OrderedDict, key, value, limit: int) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)

    def _query_vector(self, query: str) -> tuple[float, ...]:
        clean = str(query or "").strip()
        with self._lock:
            cached = self._queries.get(clean)
            if cached is not None:
                self._queries.move_to_end(clean)
                return cached
        vector = self.backend.embed_query(clean)
        with self._lock:
            self._cache_put(self._queries, clean, vector, self.max_cached_queries)
        return vector

    def _document_vectors(
        self,
        documents: Sequence[SemanticDocument],
    ) -> dict[str, tuple[float, ...]]:
        output: dict[str, tuple[float, ...]] = {}
        missing: list[tuple[SemanticDocument, str]] = []
        with self._lock:
            for document in documents:
                fingerprint = self._fingerprint(document.text)
                cached = self._documents.get(document.key)
                if cached is not None and cached[0] == fingerprint:
                    output[document.key] = cached[1]
                    self._documents.move_to_end(document.key)
                else:
                    missing.append((document, fingerprint))
        if missing:
            vectors = self.backend.embed_documents([document.text for document, _ in missing])
            if len(vectors) != len(missing):
                raise RuntimeError("Retrieval backend returned an unexpected vector count")
            with self._lock:
                for (document, fingerprint), vector in zip(missing, vectors):
                    output[document.key] = vector
                    self._cache_put(
                        self._documents,
                        document.key,
                        (fingerprint, vector),
                        self.max_cached_documents,
                    )
        return output

    def rank(
        self,
        documents: Sequence[SemanticDocument],
        query: str,
        *,
        limit: int = 8,
        min_score: float | None = None,
    ) -> list[SemanticMatch]:
        if not documents:
            return []
        query_vector = self._query_vector(query)
        vectors = self._document_vectors(documents)
        matches = [
            SemanticMatch(
                key=document.key,
                score=round(_cosine(query_vector, vectors.get(document.key, ())), 6),
            )
            for document in documents
        ]
        if min_score is not None:
            matches = [match for match in matches if match.score >= float(min_score)]
        matches.sort(key=lambda match: (-match.score, match.key))
        return matches[: max(1, int(limit))]
