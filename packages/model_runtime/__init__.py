"""Shared model-runtime primitives used across OPERLY layers.

The public orchestration boundary is Model.infer(). Provider clients remain
exported only for compatibility while callers migrate; new code should use the
contracts and registry helpers below.
"""

from .catalog import (
    ModelResource,
    has_delegate_models,
    model_resources,
    provider_is_configured,
    register_model_resource,
    replace_discovered_resources,
    select_model_resource,
)
from .contracts import (
    InferenceBudget,
    InferenceRequest,
    InferenceResult,
    Model,
    ModelInferenceError,
    ModelSelector,
    ModelTraits,
    ModelUsage,
)
from .discovery import (
    installed_model_discoverers,
    refresh_model_discovery,
    register_model_discoverer,
)
from .ollama_client import OllamaClient, OllamaError
from .openai_compatible_client import OpenAICompatibleClient
from .openrouter_client import OpenRouterClient
from .portfolio import ModelRoute, configured_portfolio, model_route
from .providers import (
    ModelClient,
    installed_model_providers,
    model_client_for_route,
    register_model_provider,
)
from .registry import (
    ConfiguredModel,
    ModelAttemptEvent,
    ModelChatAdapter,
    ModelPool,
    ModelRegistry,
    default_model_registry,
    model_chat_client_for_role,
    model_for_role,
    register_model_telemetry_sink,
)
from .routing_policy import (
    RoleRoutingProfile,
    auto_portfolio_enabled,
    configured_provider_count,
    role_routing_profile,
    role_routing_profiles,
)
from .semantic_router import SemanticDecision, SemanticRouter, SemanticRoutingError
from .service import ModelInvocationResult, ModelInvocationService

__all__ = [
    "InferenceBudget",
    "InferenceRequest",
    "InferenceResult",
    "Model",
    "ModelInferenceError",
    "ModelSelector",
    "ModelTraits",
    "ModelUsage",
    "ConfiguredModel",
    "ModelPool",
    "ModelRegistry",
    "ModelAttemptEvent",
    "ModelChatAdapter",
    "default_model_registry",
    "model_for_role",
    "model_chat_client_for_role",
    "register_model_telemetry_sink",
    "RoleRoutingProfile",
    "role_routing_profile",
    "role_routing_profiles",
    "auto_portfolio_enabled",
    "configured_provider_count",
    "provider_is_configured",
    # Compatibility exports below this line. New callers should not depend on them.
    "OllamaClient",
    "OllamaError",
    "OpenRouterClient",
    "OpenAICompatibleClient",
    "ModelClient",
    "ModelRoute",
    "ModelResource",
    "ModelInvocationResult",
    "ModelInvocationService",
    "configured_portfolio",
    "model_route",
    "model_resources",
    "select_model_resource",
    "register_model_resource",
    "replace_discovered_resources",
    "has_delegate_models",
    "installed_model_providers",
    "model_client_for_route",
    "register_model_provider",
    "installed_model_discoverers",
    "refresh_model_discovery",
    "register_model_discoverer",
    "SemanticDecision",
    "SemanticRouter",
    "SemanticRoutingError",
]
