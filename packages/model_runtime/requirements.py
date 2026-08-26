"""Capability/constraint-driven adaptive model selection."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from packages.model_runtime.contracts import (
    InferenceBudget,
    InferenceRequest,
    InferenceResult,
    Model,
    ModelSelector,
    ModelTraits,
)
from packages.model_runtime.discovery import refresh_model_discovery
from packages.model_runtime.registry import (
    ConfiguredModel,
    ModelChatAdapter,
    ModelPool,
    default_model_registry,
    model_for_role,
)


@dataclass(frozen=True, slots=True)
class ModelRequirements:
    requires: frozenset[str] = frozenset({"text"})
    prefer_tags: frozenset[str] = frozenset()
    avoid_tags: frozenset[str] = frozenset()
    prefer_free: bool = False
    max_models: int = 3
    min_context_tokens: int | None = None
    reason: str = ""

    def selector(self) -> ModelSelector:
        return ModelSelector(
            requires=frozenset(
                str(item).strip().lower() for item in self.requires if str(item).strip()
            ),
            prefer_tags=frozenset(
                str(item).strip().lower() for item in self.prefer_tags if str(item).strip()
            ),
            avoid_tags=frozenset(
                str(item).strip().lower() for item in self.avoid_tags if str(item).strip()
            ),
            prefer_free=self.prefer_free,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "requires": sorted(self.requires),
            "preferTags": sorted(self.prefer_tags),
            "avoidTags": sorted(self.avoid_tags),
            "preferFree": self.prefer_free,
            "maxModels": self.max_models,
            "minContextTokens": self.min_context_tokens,
            "reason": self.reason,
        }


def _context_rank(model: ConfiguredModel, minimum: int | None) -> int:
    if not minimum:
        return 0
    available = model.traits.context_tokens
    if available is None:
        return 1
    return 0 if int(available) >= int(minimum) else 2


def _eligible_models(requirements: ModelRequirements) -> list[ConfiguredModel]:
    registry = default_model_registry()
    candidates = [
        model
        for model in registry.candidates(requirements.selector())
        if not model.id.startswith("role:")
    ]
    minimum = requirements.min_context_tokens
    if minimum:
        candidates = [model for model in candidates if _context_rank(model, minimum) < 2]
        # Known-good context windows remain a static prior. ModelPool's live scorer
        # performs the final route ranking immediately before inference.
        candidates.sort(
            key=lambda model: (
                _context_rank(model, minimum),
                0 if (requirements.prefer_free and "free" in model.tags) else 1,
                model.priority,
                model.verified_latency_ms or 10**9,
                model.id,
            )
        )
    return candidates


class AdaptiveRequirementsModel:
    """Refresh the global model index and score all eligible routes per call."""

    def __init__(
        self,
        requirements: ModelRequirements,
        *,
        fallback_role: str | None = None,
    ) -> None:
        self.requirements = requirements
        self.fallback_role = fallback_role
        self.id = "adaptive-requirements"
        self.tags = requirements.prefer_tags
        self.capabilities = requirements.requires
        self.traits = ModelTraits(context_tokens=requirements.min_context_tokens)

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        try:
            ttl = float(os.getenv("OPERLY_MODEL_DISCOVERY_TTL_SECONDS", "600"))
        except ValueError:
            ttl = 600.0
        await refresh_model_discovery(ttl_seconds=max(0.0, ttl))
        candidates = _eligible_models(self.requirements)
        if candidates:
            # max_models is an invocation batch limit, not an index truncation. Every
            # eligible model remains available for future ranking/exploration.
            return await ModelPool(
                candidates,
                id="requirements:dynamic",
                batch_size=max(1, int(self.requirements.max_models or 1)),
            ).infer(request)
        if self.fallback_role and not self.requirements.avoid_tags:
            return await model_for_role(self.fallback_role).infer(request)
        required = ", ".join(sorted(self.requirements.requires)) or "text"
        raise LookupError(f"No model satisfies inference requirements: {required}")


def model_for_requirements(
    requirements: ModelRequirements,
    *,
    fallback_role: str | None = None,
) -> Model:
    """Return a live requirements facade instead of pre-truncating a static pool."""
    return AdaptiveRequirementsModel(requirements, fallback_role=fallback_role)


def model_chat_client_for_requirements(
    requirements: ModelRequirements,
    *,
    budget: InferenceBudget | None = None,
    fallback_role: str | None = None,
) -> ModelChatAdapter:
    return ModelChatAdapter(
        model_for_requirements(requirements, fallback_role=fallback_role),
        budget=budget,
    )
