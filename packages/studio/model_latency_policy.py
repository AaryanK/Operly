"""Studio interaction budgets expressed through provider-neutral model policy.

Studio declares the inference capabilities it needs; it does not pick a semantic
model category or provider. Attempt limits, output budgets, cross-provider
failover, and owner-only tracing remain enforced by model_runtime/Studio policy.
"""
from __future__ import annotations

from packages.coding_harness.context_window import ContextBoundCodingClient
from packages.model_runtime import InferenceBudget
from packages.model_runtime.requirements import ModelRequirements, model_chat_client_for_requirements
from packages.studio import agent_runs, runtime_policy, source_agent
from packages.studio.model_trace import TracingModelChatClient, install_agent_run_trace_context

_STUDIO_PROVIDER_ATTEMPT_SECONDS = 60
_STUDIO_MODEL_SLICE_SECONDS = 195
_STUDIO_EDIT_MAX_SECONDS = 420
_STUDIO_GENERATE_MAX_SECONDS = 600
_STUDIO_MAX_MODELS = 3
_STUDIO_MAX_OUTPUT_TOKENS = 16_384

_APPLIED = False


def studio_budget(operation: str) -> tuple[int, int, int]:
    """Return (max turns, total seconds, per-model-turn seconds) for Studio."""
    if operation == "generate":
        return 20, _STUDIO_GENERATE_MAX_SECONDS, _STUDIO_MODEL_SLICE_SECONDS
    return 10, _STUDIO_EDIT_MAX_SECONDS, _STUDIO_MODEL_SLICE_SECONDS


def studio_coding_model_client(role: str = "coding"):
    """Build Studio's traced client from concrete inference requirements.

    `role` is retained only as a compatibility fallback for deployments whose
    model catalog has not yet been enriched with capability metadata.
    """
    requirements = ModelRequirements(
        requires=frozenset({"text", "coding", "tools"}),
        prefer_tags=frozenset({"coding", "reasoning", "reliable", "verified", "fast"}),
        max_models=_STUDIO_MAX_MODELS,
        reason="Studio persistent source editing with tool use",
    )
    adapter = model_chat_client_for_requirements(
        requirements,
        budget=InferenceBudget(
            timeout_seconds=_STUDIO_PROVIDER_ATTEMPT_SECONDS,
            attempts_per_model=1,
            max_models=_STUDIO_MAX_MODELS,
            max_output_tokens=_STUDIO_MAX_OUTPUT_TOKENS,
        ),
        fallback_role=role,
    )
    return ContextBoundCodingClient(TracingModelChatClient(adapter))


class StudioLatencyAwareCodingAgent(runtime_policy.StudioWebsiteCodingAgent):
    """Website session policy layered over the generic coding agent."""

    def __init__(self, client=None, max_steps=None, registry=None, progress_callback=None) -> None:
        super().__init__(
            client=client,
            max_steps=max_steps,
            registry=registry,
            progress_callback=progress_callback,
        )
        self.max_seconds = max(self.max_seconds, _STUDIO_GENERATE_MAX_SECONDS)
        self.model_slice_seconds = max(self.model_slice_seconds, _STUDIO_MODEL_SLICE_SECONDS)


def apply_studio_model_latency_policy() -> None:
    """Install Studio deadlines and owner-only model tracing without provider coupling."""
    global _APPLIED
    if _APPLIED:
        return

    install_agent_run_trace_context()

    agent_runs._studio_budget = studio_budget
    agent_runs.coding_model_client = studio_coding_model_client
    agent_runs.OpenCodeStyleCodingAgent = StudioLatencyAwareCodingAgent

    runtime_policy._studio_budget = studio_budget

    source_agent.coding_model_client = studio_coding_model_client
    source_agent.OpenCodeStyleCodingAgent = StudioLatencyAwareCodingAgent

    _APPLIED = True
