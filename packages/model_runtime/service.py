"""Model invocation service shared by harness/plugin surfaces."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from packages.model_runtime.catalog import ModelResource, select_model_resource
from packages.model_runtime.portfolio import ModelRoute, model_route
from packages.model_runtime.providers import model_client_for_route


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

        orchestrator = model_route("business_agent")
        exclude = (
            (orchestrator.provider, orchestrator.primary)
            if exclude_orchestrator
            else None
        )
        resource = select_model_resource(
            clean_capability,
            exclude=exclude,
            prefer_free=prefer_free,
        )
        if resource is None:
            raise LookupError(f"No delegated model is registered for capability: {clean_capability}")

        client = model_client_for_route(
            ModelRoute(provider=resource.provider, primary=resource.id)
        )
        messages: list[dict[str, Any]] = [
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
        ]
        response = await client.chat(messages, [])
        content = str(response.get("content") or "").strip()
        if not content:
            raise RuntimeError("Delegated model returned no usable content")
        return ModelInvocationResult(
            provider=resource.provider,
            model=resource.id,
            capability=clean_capability,
            content=content,
        )
