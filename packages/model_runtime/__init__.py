"""Shared model-runtime primitives used across OPERLY layers."""

from .catalog import (
    ModelResource,
    has_delegate_models,
    model_resources,
    register_model_resource,
    replace_discovered_resources,
    select_model_resource,
)
from .discovery import (
    installed_model_discoverers,
    refresh_model_discovery,
    register_model_discoverer,
)
from .ollama_client import OllamaClient, OllamaError
from .openrouter_client import OpenRouterClient
from .portfolio import ModelRoute, configured_portfolio, model_route
from .providers import (
    ModelClient,
    installed_model_providers,
    model_client_for_route,
    register_model_provider,
)
from .semantic_router import SemanticDecision, SemanticRouter, SemanticRoutingError
from .service import ModelInvocationResult, ModelInvocationService

__all__ = [
    "OllamaClient",
    "OllamaError",
    "OpenRouterClient",
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
