from .contracts import (
    DependencyPolicy,
    RuntimeMatch,
    RuntimePlugin,
    RuntimePluginSpec,
    RuntimeValidation,
)
from .registry import RuntimeRegistry, default_runtime_registry


def register_builtin_runtimes(registry=None):
    """Lazy bootstrap avoids a coding-harness package import cycle."""
    from .builtins import register_builtin_runtimes as _register

    return _register(registry)


__all__ = [
    "DependencyPolicy",
    "RuntimeMatch",
    "RuntimePlugin",
    "RuntimePluginSpec",
    "RuntimeValidation",
    "RuntimeRegistry",
    "default_runtime_registry",
    "register_builtin_runtimes",
]
