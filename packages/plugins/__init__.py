"""Operly plugin/package infrastructure.

This package owns installable extension metadata and lifecycle contracts. It does not
own capability authorization: every executable capability still enters through the
Kernel runtime.
"""

from packages.plugins.contracts import (
    BindingRequest,
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
from packages.plugins.runtime_profiles import RuntimeProfile, RuntimeProfileRegistry, default_runtime_profiles

__all__ = [
    "BindingRequest",
    "EventDeclaration",
    "NetworkPolicy",
    "PluginCapability",
    "PluginExecutionMode",
    "PluginLifecycleState",
    "PluginManifest",
    "ResourcePolicy",
    "RuntimeProfile",
    "RuntimeProfileRegistry",
    "RuntimeRequirement",
    "StorageRequest",
    "UIContribution",
    "default_runtime_profiles",
]
