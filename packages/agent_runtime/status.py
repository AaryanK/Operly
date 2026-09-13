"""Shared configuration status for public runtime discovery endpoints."""
from packages.agent_runtime.inference import AgentInferenceError, InferenceRoute
from packages.agent_runtime.runtime import AgentRuntimeSettings


def agent_runtime_status() -> dict[str, object]:
    enabled = AgentRuntimeSettings.from_environment().enabled
    if not enabled:
        return {
            "enabled": False,
            "configured": False,
            "provider": None,
            "model": None,
            "reason": "OPERLY_AGENT_RUNTIME_ENABLED is off",
        }
    try:
        route = InferenceRoute.from_environment()
    except AgentInferenceError as error:
        return {
            "enabled": True,
            "configured": False,
            "provider": None,
            "model": None,
            "reason": str(error),
        }
    return {
        "enabled": True,
        "configured": True,
        "provider": route.provider,
        "model": route.model_id,
        "reason": None,
    }

