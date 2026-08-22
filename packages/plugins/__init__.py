"""Plugin manifests and lifecycle runtime."""

from .manifest import (
    EventSpec,
    PermissionSpec,
    PluginLifecycleSpec,
    PluginManifest,
    PluginManifestRegistry,
    ToolManifest,
)
from .runtime import (
    PluginContribution,
    PluginHealthResult,
    PluginLifecycle,
    PluginRuntime,
    default_plugin_runtime,
)

__all__ = [
    "EventSpec",
    "PermissionSpec",
    "PluginLifecycleSpec",
    "PluginManifest",
    "PluginManifestRegistry",
    "ToolManifest",
    "PluginContribution",
    "PluginHealthResult",
    "PluginLifecycle",
    "PluginRuntime",
    "default_plugin_runtime",
]
