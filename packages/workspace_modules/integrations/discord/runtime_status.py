from __future__ import annotations

from packages.agent_runtime.inference import AgentInferenceError, InferenceRoute
from packages.agent_runtime.runtime import AgentRuntimeSettings


def discord_ai_runtime_status() -> tuple[bool, str]:
    if not AgentRuntimeSettings.from_environment().enabled:
        return False, "Agent Runtime 1.0 is disabled by deployment policy."
    try:
        route = InferenceRoute.from_environment()
    except AgentInferenceError as error:
        return False, str(error)
    return True, f"Agent Runtime 1.0 is ready with {route.provider}/{route.model_id}."
