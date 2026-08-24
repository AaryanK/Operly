"""Capability/constraint-driven model selection.

Roles remain a compatibility and observability label. New execution paths select
models from concrete inference requirements so a mixed task does not have to be
force-fit into one semantic bucket before the model portfolio can be consulted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from packages.model_runtime.contracts import InferenceBudget, Model, ModelSelector
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
                str(item).strip().lower()
                for item in self.requires
                if str(item).strip()
            ),
            prefer_tags=frozenset(
                str(item).strip().lower()
                for item in self.prefer_tags
                if str(item).strip()
            ),
            avoid_tags=frozenset(
                str(item).strip().lower()
                for item in self.avoid_tags
                if str(item).strip()
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


def _provider_diverse(
    candidates: Iterable[ConfiguredModel],
    limit: int,
) -> list[ConfiguredModel]:
    rows = list(candidates)
    selected: list[ConfiguredModel] = []
    seen: set[str] = set()
    for model in rows:
        if model.provider in seen:
            continue
        selected.append(model)
        seen.add(model.provider)
        if len(selected) >= limit:
            return selected
    for model in rows:
        if model in selected:
            continue
        selected.append(model)
        if len(selected) >= limit:
            break
    return selected


def _flatten(model: Model) -> list[ConfiguredModel]:
    if isinstance(model, ConfiguredModel):
        return [model]
    if isinstance(model, ModelPool):
        return [item for item in model.models if isinstance(item, ConfiguredModel)]
    return []


def _compatible_fallbacks(
    selected: list[ConfiguredModel],
    *,
    requirements: ModelRequirements,
    fallback_role: str | None,
    limit: int,
) -> list[ConfiguredModel]:
    """Backfill pool slots without bypassing concrete model constraints.

    The legacy role chain is a compatibility source, never an escape hatch from
    `requires`, `avoid_tags`, or context-window constraints. This is especially
    important for small-worker pools: an explicit `heavy` exclusion must survive
    provider failover, otherwise a routine turn could silently become a deep-model
    invocation.
    """
    if not fallback_role or len(selected) >= limit:
        return selected
    try:
        fallback_models = _flatten(model_for_role(fallback_role))
    except (LookupError, RuntimeError):
        return selected

    required = set(requirements.requires)
    avoided = set(requirements.avoid_tags)
    minimum = requirements.min_context_tokens
    seen = {
        (str(model.provider), str(model.provider_model_id))
        for model in selected
    }
    for model in fallback_models:
        identity = (str(model.provider), str(model.provider_model_id))
        if identity in seen:
            continue
        if not required.issubset(set(model.capabilities)):
            continue
        if avoided & set(model.tags):
            continue
        if minimum and _context_rank(model, minimum) >= 2:
            continue
        selected.append(model)
        seen.add(identity)
        if len(selected) >= limit:
            break
    return selected


def model_for_requirements(
    requirements: ModelRequirements,
    *,
    fallback_role: str | None = None,
) -> Model:
    """Resolve a provider-diverse model pool from concrete requirements.

    Known models that cannot meet the requested context window are excluded.
    Models with unknown context metadata remain eligible after known-good models;
    this avoids turning incomplete catalog metadata into a hard outage.

    Compatibility `role:*` resources are deliberately excluded from the dynamic
    candidate scan. They are mutable process-local projections of role/env state
    and can remain in the registry after a prior lookup. Mixing those stale rows
    into capability selection caused duplicate/stale Studio fallbacks. The current
    role chain is consulted only by `_compatible_fallbacks` after dynamic selection,
    and must obey the same concrete requirements.
    """
    registry = default_model_registry()
    candidates = [
        model
        for model in registry.candidates(requirements.selector())
        if not model.id.startswith("role:")
    ]
    minimum = requirements.min_context_tokens
    if minimum:
        candidates = [
            model for model in candidates if _context_rank(model, minimum) < 2
        ]
        candidates.sort(
            key=lambda model: (
                _context_rank(model, minimum),
                0 if (requirements.prefer_free and "free" in model.tags) else 1,
                model.priority,
                model.verified_latency_ms or 10**9,
                model.id,
            )
        )

    limit = max(1, int(requirements.max_models or 1))
    selected = _provider_diverse(candidates, limit)
    selected = _compatible_fallbacks(
        selected,
        requirements=requirements,
        fallback_role=fallback_role,
        limit=limit,
    )
    if selected:
        return (
            selected[0]
            if len(selected) == 1
            else ModelPool(selected, id="requirements:dynamic")
        )
    # An unconstrained caller may still use the old role chain as a final migration
    # fallback. If the caller explicitly excluded a model class, never erase that
    # policy merely to keep the request alive.
    if fallback_role and not requirements.avoid_tags:
        return model_for_role(fallback_role)
    required = ", ".join(sorted(requirements.requires)) or "text"
    raise LookupError(f"No model satisfies inference requirements: {required}")


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
