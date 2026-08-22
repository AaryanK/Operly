"""Model invocation service shared by harness/plugin surfaces."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from packages.model_runtime.contracts import InferenceRequest, ModelSelector
from packages.model_runtime.discovery import refresh_model_discovery
from packages.model_runtime.portfolio import model_route
from packages.model_runtime.registry import default_model_registry


@dataclass(frozen=True, slots=True)
class ModelInvocationResult:
    provider: str
    model: str
    capability: str
    content: str


class ModelInvocationService:
    """Route a bounded specialist request by capability, not model identity.

    Delegated calls intentionally receive no tools. This makes model-as-tool
    delegation one level deep by default and prevents unbounded model recursion.
    Selection and provider transport stay inside model_runtime.
    """

    async def invoke(
        self,
        *,
        capability: str,
        objective: str,
        context: str = "",
        prefer_free: bool = True,
        exclude_orchestrator: bool = True,
    ) -> ModelInvocationResult:
        clean_capability = str(capability or "").strip().lower()
        clean_objective = " ".join(str(objective or "").split()).strip()
        if not clean_capability or not clean_objective:
            raise ValueError("Model capability and objective are required")

        try:
            ttl = float(os.getenv("OPERLY_MODEL_DISCOVERY_TTL_SECONDS", "600"))
        except ValueError:
            ttl = 600.0
        await refresh_model_discovery(ttl_seconds=max(0.0, ttl))

        excluded: set[str] = set()
        if exclude_orchestrator:
            orchestrator = model_route("business_agent")
            excluded.add(f"{orchestrator.provider}:{orchestrator.primary}")

        registry = default_model_registry()
        model = registry.resolve(
            ModelSelector(
                requires=frozenset({clean_capability}),
                prefer_tags=frozenset({"free"}) if prefer_free else frozenset(),
                prefer_free=prefer_free,
                exclude_resource_ids=frozenset(excluded),
            )
        )
        messages: tuple[dict[str, Any], ...] = (
            {
                "role": "system",
                "content": (
                    "You are a bounded specialist model invoked by OPERLY. "
                    "Complete only the supplied objective. You have no tools and must "
                    "not claim external actions. Return a concise, directly usable result."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Capability: {clean_capability}\n"
                    f"Objective: {clean_objective}\n"
                    + (f"Context:\n{str(context)[:12000]}" if context else "")
                ),
            },
        )
        result = await model.infer(InferenceRequest(messages=messages))
        content = str(result.message.get("content") or "").strip()
        if not content:
            raise RuntimeError("Delegated model returned no usable content")
        return ModelInvocationResult(
            provider=result.provider,
            model=result.provider_model_id,
            capability=clean_capability,
            content=content,
        )
