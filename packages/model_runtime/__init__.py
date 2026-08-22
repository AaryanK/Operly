"""Shared model-runtime primitives used across OPERLY layers."""

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

__all__ = [
    "OllamaClient",
    "OllamaError",
    "OpenRouterClient",
    "ModelClient",
    "ModelRoute",
    "configured_portfolio",
    "model_route",
    "installed_model_providers",
    "model_client_for_route",
    "register_model_provider",
    "SemanticDecision",
    "SemanticRouter",
    "SemanticRoutingError",
]
