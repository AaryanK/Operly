"""Shared local semantic retrieval primitives.

Production defaults to a small ONNX-backed FastEmbed model. Tests and degraded
runtime environments may explicitly use the deterministic hashing backend. Search
callers still own authorization: this module only ranks documents supplied to it.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import blake2b
from math import sqrt
import os
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
    """Deterministic no-network fallback used only when embeddings are unavailable."""

    def __init__(self, *, dimensions: int = 384, reason: str | None = None) -> None:
        self.dimensions = max(64, int(dimensions))
        self._reason = reason

    @property
    def name(self) -> str:
        return f"hashing:{self.dimensions}"

    @property
    def degraded_reason(self) -> str | None:
        return self._reason or "deterministic_hashing_backend"

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


class FastEmbedBackend:
    """Lazy local ONNX embedding backend.

    The model is downloaded/cached by fastembed once, then all searches are local.
    No user context or capability metadata is sent to an external embedding API.
    """

    def __init__(self, *, model_name: str | None = None) -> None:
        self.model_name = str(
            model_name
            or os.getenv("OPERLY_SEMANTIC_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
        ).strip()
        self._model = None
        self._lock = RLock()

    @property
    def name(self) -> str:
        return f"fastembed:{self.model_name}"

    @property
    def degraded_reason(self) -> str | None:
        return None

    def _get_model(self):
        with self._lock:
            if self._model is not None:
                return self._model
            from fastembed import TextEmbedding

            kwargs = {"model_name": self.model_name}
            cache_dir = os.getenv("OPERLY_SEMANTIC_EMBEDDING_CACHE", "").strip()
            if cache_dir:
                kwargs["cache_dir"] = cache_dir
            self._model = TextEmbedding(**kwargs)
            return self._model

    @staticmethod
    def _vectors(rows) -> list[tuple[float, ...]]:
        return [_normalized(row.tolist() if hasattr(row, "tolist") else row) for row in rows]

    def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []
        model = self._get_model()
        method = getattr(model, "passage_embed", None)
        rows = method(list(texts)) if callable(method) else model.embed(list(texts))
        return self._vectors(rows)

    def embed_query(self, text: str) -> tuple[float, ...]:
        model = self._get_model()
        method = getattr(model, "query_embed", None)
        rows = method([str(text or "")]) if callable(method) else model.embed([str(text or "")])
        vectors = self._vectors(rows)
        return vectors[0] if vectors else ()


class AutoEmbeddingBackend:
    """Use real local embeddings by default and degrade safely if initialization fails."""

    def __init__(self) -> None:
        self._delegate: EmbeddingBackend = FastEmbedBackend()
        self._degraded_reason: str | None = None
        self._lock = RLock()

    @property
    def name(self) -> str:
        return self._delegate.name

    @property
    def degraded_reason(self) -> str | None:
        return self._degraded_reason or self._delegate.degraded_reason

    def _fallback(self, error: BaseException) -> EmbeddingBackend:
        with self._lock:
            if isinstance(self._delegate, HashingEmbeddingBackend):
                return self._delegate
            self._degraded_reason = f"fastembed_unavailable:{type(error).__name__}"
            self._delegate = HashingEmbeddingBackend(reason=self._degraded_reason)
            return self._delegate

    def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        try:
            return self._delegate.embed_documents(texts)
        except Exception as error:
            return self._fallback(error).embed_documents(texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        try:
            return self._delegate.embed_query(text)
        except Exception as error:
            return self._fallback(error).embed_query(text)


_BACKEND: EmbeddingBackend | None = None
_BACKEND_LOCK = RLock()


def embedding_backend() -> EmbeddingBackend:
    global _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND is not None:
            return _BACKEND
        mode = os.getenv("OPERLY_SEMANTIC_EMBEDDING_BACKEND", "auto").strip().lower()
        if mode in {"hash", "hashing", "deterministic"}:
            _BACKEND = HashingEmbeddingBackend()
        elif mode in {"fastembed", "onnx"}:
            _BACKEND = FastEmbedBackend()
        else:
            _BACKEND = AutoEmbeddingBackend()
        return _BACKEND


class SemanticTextIndex:
    """Exact cosine search with process-local document/query embedding caches."""

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
                raise RuntimeError("Embedding backend returned an unexpected vector count")
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
            SemanticMatch(key=document.key, score=round(_cosine(query_vector, vectors.get(document.key, ())), 6))
            for document in documents
        ]
        if min_score is not None:
            matches = [match for match in matches if match.score >= float(min_score)]
        matches.sort(key=lambda match: (-match.score, match.key))
        return matches[: max(1, int(limit))]
