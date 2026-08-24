"""Provider-neutral role profiles for automatic multi-model orchestration."""
from __future__ import annotations

import os
from dataclasses import dataclass

from packages.model_runtime.catalog import provider_is_configured
from packages.model_runtime.contracts import ModelSelector


@dataclass(frozen=True, slots=True)
class RoleRoutingProfile:
    role: str
    requires: frozenset[str]
    prefer_tags: frozenset[str]
    avoid_tags: frozenset[str] = frozenset()
    prefer_free: bool = True
    max_models: int = 5

    def selector(self) -> ModelSelector:
        return ModelSelector(
            requires=self.requires,
            prefer_tags=self.prefer_tags,
            avoid_tags=self.avoid_tags,
            prefer_free=self.prefer_free,
        )

    def as_dict(self) -> dict:
        return {
            "role": self.role,
            "requires": sorted(self.requires),
            "prefer_tags": sorted(self.prefer_tags),
            "avoid_tags": sorted(self.avoid_tags),
            "prefer_free": self.prefer_free,
            "max_models": self.max_models,
        }


_PROFILES: dict[str, RoleRoutingProfile] = {
    "router": RoleRoutingProfile(
        "router",
        frozenset({"text", "reasoning"}),
        frozenset({"fast", "small", "verified", "reliable", "free"}),
        max_models=2,
    ),
    # Ordinary manager/worker turns should prefer genuinely small, fast resources.
    # Do not reward the legacy ``orchestrator`` compatibility tag here: the legacy
    # route may point at a heavy model (for example Ox Alpha), which would otherwise
    # win tie-breaks despite the small-model-first runtime contract. Strong reasoning
    # remains explicitly available through model.deep_reason.
    "business_agent": RoleRoutingProfile(
        "business_agent",
        frozenset({"text", "tools"}),
        frozenset({"small", "verified", "fast", "reliable", "free"}),
        avoid_tags=frozenset({"slow"}),
        max_models=3,
    ),
    "coding": RoleRoutingProfile(
        "coding",
        frozenset({"text", "coding", "tools"}),
        frozenset({"coding", "small", "verified", "fast", "reliable"}),
        avoid_tags=frozenset({"slow"}),
        max_models=3,
    ),
    "repair": RoleRoutingProfile(
        "repair",
        frozenset({"text", "coding", "tools"}),
        frozenset({"coding", "reasoning", "verified", "reliable", "heavy"}),
    ),
    "planner": RoleRoutingProfile(
        "planner",
        frozenset({"text", "reasoning"}),
        frozenset({"reasoning", "verified", "heavy", "long-context", "reliable"}),
    ),
    "global_validator": RoleRoutingProfile(
        "global_validator",
        frozenset({"text", "reasoning"}),
        frozenset({"reasoning", "verified", "heavy", "reliable"}),
    ),
    "requirements_analyst": RoleRoutingProfile(
        "requirements_analyst",
        frozenset({"text", "reasoning"}),
        frozenset({"reasoning", "small", "verified", "fast", "reliable", "free"}),
        max_models=3,
    ),
    "capability_placement": RoleRoutingProfile(
        "capability_placement",
        frozenset({"text", "reasoning"}),
        frozenset({"reasoning", "small", "verified", "fast"}),
        max_models=3,
    ),
    "bounded_task": RoleRoutingProfile(
        "bounded_task",
        frozenset({"text", "tools"}),
        frozenset({"fast", "small", "verified", "free", "reliable"}),
        avoid_tags=frozenset({"slow"}),
        max_models=3,
    ),
    "attachment_text": RoleRoutingProfile(
        "attachment_text",
        frozenset({"text", "reasoning"}),
        frozenset({"fast", "small", "verified", "reliable", "free"}),
        max_models=3,
    ),
    "attachment_vision": RoleRoutingProfile(
        "attachment_vision",
        frozenset({"text", "reasoning", "vision"}),
        frozenset({"fast", "small", "verified", "reliable", "free"}),
        max_models=3,
    ),
}


def role_routing_profile(role: str) -> RoleRoutingProfile:
    key = str(role or "bounded_task").strip().lower()
    return _PROFILES.get(key, _PROFILES["bounded_task"])


def role_routing_profiles() -> dict[str, dict]:
    return {name: profile.as_dict() for name, profile in sorted(_PROFILES.items())}


def configured_provider_count() -> int:
    return sum(
        1
        for provider in ("openrouter", "ollama", "groq", "gemini", "nvidia")
        if provider_is_configured(provider)
    )


def auto_portfolio_enabled() -> bool:
    value = os.getenv("OPERLY_MODEL_AUTO_PORTFOLIO", "1").strip().lower()
    if value in {"0", "false", "no", "off"}:
        return False
    # Automatic capability/tag routing is useful even with one provider because a
    # provider may expose multiple models with very different latency/cost profiles.
    return configured_provider_count() >= 1
