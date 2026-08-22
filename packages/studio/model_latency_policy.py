"""Studio-only model latency and provider-failover policy.

Studio is an interactive durable coding surface. A single preview or free provider
must not be able to hold an owner-visible edit for minutes and then fail without a
model response. Keep the shared provider adapters unchanged; Studio gets a bounded
first-response window plus a small provider/model fallback chain.
"""
from __future__ import annotations

import os

from packages.coding_harness.model_client import coding_model_client as _shared_coding_model_client
from packages.model_runtime.openrouter_client import OpenRouterClient
from packages.studio import agent_runs, runtime_policy, source_agent

# One provider/model request gets at most one minute in Studio. With the primary and
# two fallbacks this remains below the 195-second outer model-turn ceiling.
_STUDIO_PROVIDER_TIMEOUT_SECONDS = 60
_STUDIO_MODEL_SLICE_SECONDS = 195
_STUDIO_EDIT_MAX_SECONDS = 420
_STUDIO_GENERATE_MAX_SECONDS = 600
_STUDIO_MAX_FALLBACK_MODELS = 2

# Prefer a free, broadly hosted tool-capable model first. Keep a fast dedicated
# coding model as the paid last resort. Operators can replace this list through
# OPERLY_STUDIO_OPENROUTER_FALLBACKS without changing harness code.
_DEFAULT_OPENROUTER_FALLBACKS = (
    "openai/gpt-oss-120b:free",
    "qwen/qwen3-coder-flash",
)

_APPLIED = False


def _studio_fallbacks(primary: str, configured: list[str] | tuple[str, ...]) -> list[str]:
    explicit = os.getenv("OPERLY_STUDIO_OPENROUTER_FALLBACKS", "").strip()
    if explicit:
        candidates = [item.strip() for item in explicit.split(",") if item.strip()]
    elif configured:
        candidates = [str(item).strip() for item in configured if str(item).strip()]
    else:
        candidates = list(_DEFAULT_OPENROUTER_FALLBACKS)

    selected: list[str] = []
    for model in candidates:
        if not model or model == primary or model in selected:
            continue
        selected.append(model)
        if len(selected) >= _STUDIO_MAX_FALLBACK_MODELS:
            break
    return selected


def studio_budget(operation: str) -> tuple[int, int, int]:
    """Return (max turns, total seconds, per-model-turn seconds) for Studio."""
    if operation == "generate":
        return 20, _STUDIO_GENERATE_MAX_SECONDS, _STUDIO_MODEL_SLICE_SECONDS
    return 10, _STUDIO_EDIT_MAX_SECONDS, _STUDIO_MODEL_SLICE_SECONDS


def studio_coding_model_client(role: str = "coding"):
    """Build the normal provider-neutral client, then apply Studio-only failover."""
    client = _shared_coding_model_client(role)
    inner = getattr(client, "inner", None)
    if isinstance(inner, OpenRouterClient):
        inner.timeout_seconds = min(
            int(inner.timeout_seconds),
            _STUDIO_PROVIDER_TIMEOUT_SECONDS,
        )
        # Retrying the exact same reasoning request delays useful failover. Studio
        # instead makes one attempt per model and moves to the next configured model.
        inner.max_attempts = 1
        inner.fallback_models = _studio_fallbacks(inner.model, inner.fallback_models)
        inner.fallback_model = inner.fallback_models[0] if inner.fallback_models else ""
    return client


class StudioLatencyAwareCodingAgent(runtime_policy.StudioWebsiteCodingAgent):
    """Website agent whose internal ceilings are high enough for Studio's policy."""

    def __init__(self, client=None, max_steps=None, registry=None, progress_callback=None) -> None:
        super().__init__(
            client=client,
            max_steps=max_steps,
            registry=registry,
            progress_callback=progress_callback,
        )
        # CapabilityCodingAgent defaults to a 240s total / 90s slice. The durable
        # runner later clamps these values to studio_budget(). Raise the instance
        # ceilings first so the Studio-specific 420/600s and 195s limits are real.
        self.max_seconds = max(self.max_seconds, _STUDIO_GENERATE_MAX_SECONDS)
        self.model_slice_seconds = max(
            self.model_slice_seconds,
            _STUDIO_MODEL_SLICE_SECONDS,
        )


def apply_studio_model_latency_policy() -> None:
    """Install the deadline/failover hierarchy after website runtime policy."""
    global _APPLIED
    if _APPLIED:
        return

    agent_runs._studio_budget = studio_budget
    agent_runs.coding_model_client = studio_coding_model_client
    agent_runs.OpenCodeStyleCodingAgent = StudioLatencyAwareCodingAgent

    runtime_policy._studio_budget = studio_budget

    source_agent.coding_model_client = studio_coding_model_client
    source_agent.OpenCodeStyleCodingAgent = StudioLatencyAwareCodingAgent

    _APPLIED = True
