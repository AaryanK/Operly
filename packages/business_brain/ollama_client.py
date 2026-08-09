"""Compatibility import for the shared OPERLY model runtime.

New code should import the provider client from ``packages.model_runtime``. This
module remains so existing business-layer imports keep working during migration.
"""

from packages.model_runtime.ollama_client import OllamaClient, OllamaError

__all__ = ["OllamaClient", "OllamaError"]
