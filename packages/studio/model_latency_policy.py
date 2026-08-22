"""Studio-only model latency policy.

The shared provider adapters intentionally support generous retries because they are
used by background and non-interactive workloads too. Studio is different: one owner
is waiting on a durable, visible coding run. Its outer model-turn timeout must never
expire before the provider's own request timeout, otherwise a healthy slow reasoning
model is cancelled before the provider can return a response or a useful error.

Keep this policy local to Studio so switching providers/models remains configuration
and unrelated Operly harnesses retain their existing retry/timeout semantics.
"""
from __future__ import annotations

from packages.coding_harness.model_client import coding_model_client as _shared_coding_model_client
from packages.model_runtime.openrouter_client import OpenRouterClient
from packages.studio import agent_runs, runtime_policy, source_agent

# OpenRouter's shared adapter defaults to a 180 second request timeout. Studio gives
# that request a small outer margin so provider timeout/error handling wins the race
# instead of asyncio.wait_for cancelling the call first.
_STUDIO_PROVIDER_TIMEOUT_SECONDS = 180
_STUDIO_MODEL_SLICE_SECONDS = 195

# Allow multiple rich model/tool turns while keeping one interactive edit bounded.
# A full provider timeout can therefore happen without consuming the entire run.
_STUDIO_EDIT_MAX_SECONDS = 420
_STUDIO_GENERATE_MAX_SECONDS = 600

_APPLIED = False


def studio_budget(operation: str) -> tuple[int, int, int]:
    """Return (max turns, total seconds, per-model-turn seconds) for Studio."""
    if operation == "generate":
        return 20, _STUDIO_GENERATE_MAX_SECONDS, _STUDIO_MODEL_SLICE_SECONDS
    return 10, _STUDIO_EDIT_MAX_SECONDS, _STUDIO_MODEL_SLICE_SECONDS


def studio_coding_model_client(role: str = "coding"):
    """Build the normal provider-neutral client, then align Studio-only deadlines."""
    client = _shared_coding_model_client(role)
    inner = getattr(client, "inner", None)
    if isinstance(inner, OpenRouterClient):
        # Respect an explicitly tighter provider timeout, but never allow Studio's
        # provider request to exceed the outer per-turn budget.
        inner.timeout_seconds = min(
            int(inner.timeout_seconds),
            _STUDIO_PROVIDER_TIMEOUT_SECONDS,
        )
        # Retrying the same reasoning request up to three times can outlive the
        # interactive run and provides no new evidence. Model fallbacks, when
        # configured on the route, remain available after this one primary attempt.
        inner.max_attempts = 1
    return client


def apply_studio_model_latency_policy() -> None:
    """Install the deadline hierarchy after the website runtime policy is applied."""
    global _APPLIED
    if _APPLIED:
        return

    # The durable Studio runner resolves these module globals when each run starts.
    agent_runs._studio_budget = studio_budget
    agent_runs.coding_model_client = studio_coding_model_client

    # Keep the policy source aligned so a later explicit runtime-policy application
    # cannot silently restore the old 75/90 second model slices.
    runtime_policy._studio_budget = studio_budget

    # Legacy direct source endpoints use the same provider-neutral coding boundary.
    source_agent.coding_model_client = studio_coding_model_client

    _APPLIED = True
