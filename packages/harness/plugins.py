"""Deprecated import bridge for application-controlled plugin extensions.

The implementation moved to ``packages.plugins.extensions``. This module exists
only so remaining transport/tests can migrate without restoring the retired agent
harness. Do not add behavior here.
"""
from packages.plugins.extensions import (
    RuntimePlugin,
    RuntimePluginContext,
    RuntimePluginRegistry,
    RuntimePluginUnavailable,
    default_runtime_plugins,
)

__all__ = [
    "RuntimePlugin",
    "RuntimePluginContext",
    "RuntimePluginRegistry",
    "RuntimePluginUnavailable",
    "default_runtime_plugins",
]
