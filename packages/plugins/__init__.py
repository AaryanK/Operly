"""Plugin manifests, lifecycle runtime, and trusted application extensions."""

from .events import emit_workspace_event
from .extensions import (
    ApplicationPlugin,
    ApplicationPluginContext,
    ApplicationPluginRegistry,
    ApplicationPluginUnavailable,
    default_application_plugins,
)
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
    "ApplicationPlugin",
    "ApplicationPluginContext",
    "ApplicationPluginRegistry",
    "ApplicationPluginUnavailable",
    "default_application_plugins",
    "emit_workspace_event",
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
