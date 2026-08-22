"""Provider-neutral model contracts for every OPERLY harness.

Nothing in this module knows OpenRouter, Ollama, or any future inference vendor.
Provider adapters translate these contracts below the model-runtime boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

JSONValue = Any
Message = dict[str, Any]
ToolSchema = dict[str, Any]
ModelInput = dict[str, Any]


@dataclass(frozen=True, slots=True)
class InferenceBudget:
    """Provider-neutral inference limits selected by the caller/session policy."""

    timeout_seconds: float | None = None
    attempts_per_model: int = 1
    max_models: int | None = None


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ModelTraits:
    latency_class: str | None = None
    cost_class: str | None = None
    quality_class: str | None = None
    context_tokens: int | None = None
    locality: str | None = None


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...] = ()
    response_schema: dict[str, Any] | None = None
    modality_inputs: tuple[ModelInput, ...] = ()
    budget: InferenceBudget | None = None
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InferenceResult:
    message: Message
    model_resource_id: str
    provider: str
    provider_model_id: str
    latency_ms: int
    usage: ModelUsage | None = None
    finish_reason: str | None = None
    attempt: int = 1


@dataclass(frozen=True, slots=True)
class ModelSelector:
    """Requirements and preferences used to resolve a model resource."""

    requires: frozenset[str] = frozenset({"text"})
    prefer_tags: frozenset[str] = frozenset()
    avoid_tags: frozenset[str] = frozenset()
    prefer_free: bool = True
    exclude_resource_ids: frozenset[str] = frozenset()


class Model(Protocol):
    id: str
    tags: frozenset[str]
    capabilities: frozenset[str]
    traits: ModelTraits

    async def infer(self, request: InferenceRequest) -> InferenceResult: ...


class ModelInferenceError(RuntimeError):
    """Normalized inference failure independent of the provider transport."""

    def __init__(
        self,
        message: str,
        *,
        classification: str = "model_error",
        retryable: bool = False,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.classification = classification
        self.retryable = retryable
        self.provider = provider
        self.model_id = model_id
