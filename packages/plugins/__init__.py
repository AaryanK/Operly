"""Operly plugin/package infrastructure.

This package owns installable extension metadata and lifecycle contracts. It does not
own capability authorization: every executable capability still enters through the
Kernel runtime.
"""

from packages.plugins.contracts import (
    BindingRequest,
    CredentialRequest,
    EventDeclaration,
    NetworkPolicy,
    PluginCapability,
    PluginExecutionMode,
    PluginLifecycleState,
    PluginManifest,
    ResourcePolicy,
    RuntimeRequirement,
    StorageRequest,
    UIContribution,
)
from packages.plugins.runtime_controller import (
    PluginBuildRequest,
    PluginBuildResult,
    PluginRuntimeController,
    PluginRuntimeStatus,
    PluginStartRequest,
)
from packages.plugins.runtime_profiles import (
    RuntimeProfile,
    RuntimeProfileRegistry,
    default_runtime_profiles,
)

__all__ = [
    "BindingRequest",
    "CredentialRequest",
    "EventDeclaration",
    "NetworkPolicy",
    "PluginBuildRequest",
    "PluginBuildResult",
    "PluginCapability",
    "PluginExecutionMode",
    "PluginLifecycleState",
    "PluginManifest",
    "PluginRuntimeController",
    "PluginRuntimeStatus",
    "PluginStartRequest",
    "ResourcePolicy",
    "RuntimeProfile",
    "RuntimeProfileRegistry",
    "RuntimeRequirement",
    "StorageRequest",
    "UIContribution",
    "default_runtime_profiles",
]
