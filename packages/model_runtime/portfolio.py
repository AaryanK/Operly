"""Provider-neutral role routing for OPERLY's model portfolio.

Set OPERLY_MODEL_PROVIDER + OPERLY_MODEL_DEFAULT to move every role to another
registered provider/model. Role-specific variables override the global route.

Compatibility routes may carry provider-local fallback model IDs. Cross-provider
fallback belongs to the configured Model candidate pool (`*_CANDIDATES_JSON`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelRoute:
    provider: str
    primary: str
    fallbacks: tuple[str, ...] = ()


_DEFAULT_PROVIDER = "openrouter"
_DEFAULT_MODEL = "stealth/ox-alpha"
_DEFAULT_CODING_FALLBACKS = (
    "openai/gpt-oss-120b:free",
    "qwen/qwen3-coder-flash",
)

_DEFAULTS = {
    role: ModelRoute(_DEFAULT_PROVIDER, _DEFAULT_MODEL)
    for role in (
        "router",
        "requirements_analyst",
        "planner",
        "global_validator",
        "coding",
        "repair",
        "capability_placement",
        "business_agent",
        "bounded_task",
        "attachment_text",
        "attachment_vision",
    )
}
# These are compatibility defaults for the default OpenRouter portfolio only.
# Studio does not know these model IDs. If the operator selects another provider,
# provider-local defaults are disabled unless explicitly configured for that role.
_DEFAULTS["coding"] = ModelRoute(
    _DEFAULT_PROVIDER,
    _DEFAULT_MODEL,
    _DEFAULT_CODING_FALLBACKS,
)
_DEFAULTS["repair"] = ModelRoute(
    _DEFAULT_PROVIDER,
    _DEFAULT_MODEL,
    _DEFAULT_CODING_FALLBACKS,
)


def model_route(role: str) -> ModelRoute:
    key = str(role or "bounded_task").strip().lower()
    default = _DEFAULTS.get(key, _DEFAULTS["bounded_task"])
    env_key = "OPERLY_MODEL_" + "".join(
        character if character.isalnum() else "_" for character in key.upper()
    )
    global_provider = os.getenv("OPERLY_MODEL_PROVIDER", default.provider).strip().lower()
    global_model = os.getenv("OPERLY_MODEL_DEFAULT", default.primary).strip()
    primary = os.getenv(env_key, global_model).strip()
    provider = os.getenv(env_key + "_PROVIDER", global_provider).strip().lower()

    explicit_fallbacks = os.getenv(env_key + "_FALLBACKS")
    if explicit_fallbacks is None:
        # Never reinterpret another provider's model IDs as belonging to the newly
        # selected provider. Cross-provider failover is represented by ModelPool.
        fallback_text = ",".join(default.fallbacks) if provider == default.provider else ""
    else:
        fallback_text = explicit_fallbacks

    fallbacks = tuple(
        item.strip()
        for item in fallback_text.split(",")
        if item.strip() and item.strip() != primary
    )
    return ModelRoute(provider=provider, primary=primary, fallbacks=fallbacks)


def configured_portfolio() -> dict[str, dict]:
    return {
        role: {
            "provider": route.provider,
            "primary": route.primary,
            "fallbacks": list(route.fallbacks),
        }
        for role in _DEFAULTS
        for route in [model_route(role)]
    }
