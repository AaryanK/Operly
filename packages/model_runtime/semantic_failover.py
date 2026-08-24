"""Semantic rejection for model responses that are transport-successful but unusable.

Provider adapters can return HTTP/model success while a higher-level protocol still
fails (for example, a coding model that ignores the supplied function tools). The
model runtime owns candidate failover, so callers report that semantic mismatch here
rather than hard-coding provider/model selection in the caller.
"""
from __future__ import annotations

from packages.model_runtime.contracts import InferenceResult, ModelInferenceError
from packages.model_runtime.registry import ModelPool


def reject_model_result(
    model,
    result: InferenceResult | None,
    *,
    classification: str,
    detail: str,
) -> bool:
    """Cool only the candidate that produced ``result`` and allow pool failover.

    Returns ``False`` when there is no multi-model pool to fail over within. Semantic
    protocol mismatches are deliberately model-local; they must not cool an entire
    provider the way authentication, quota, or provider outages do.
    """
    if result is None or not isinstance(model, ModelPool):
        return False

    resource_id = str(result.model_resource_id or "").strip()
    candidate = next((item for item in model.models if item.id == resource_id), None)
    if candidate is None:
        return False

    error = ModelInferenceError(
        detail or "Model response did not satisfy the required protocol",
        classification=str(classification or "protocol_mismatch")[:80],
        retryable=True,
        provider=str(getattr(candidate, "provider", result.provider) or ""),
        model_id=str(
            getattr(candidate, "provider_model_id", result.provider_model_id)
            or result.provider_model_id
            or candidate.id
        ),
    )
    model._mark_failure(candidate, error)
    return True
