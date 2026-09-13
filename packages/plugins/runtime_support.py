"""Compatibility facade for registry-derived support, separate from manifest syntax."""
from __future__ import annotations

from typing import Any

from packages.plugins.contracts import PluginContractError, PluginExecutionMode, PluginManifest
from packages.plugins.runtime_registry import (
    DECLARED_MODE_BY_KIND, RuntimeAdapter, get_runtime_registry,
)


def runtime_mode_support(mode: PluginExecutionMode) -> dict[str, Any]:
    return get_runtime_registry().support(mode)


def runtime_profile_support(profile: str, kind: str) -> dict[str, Any]:
    return get_runtime_registry().support(
        DECLARED_MODE_BY_KIND[kind], profile=profile, kind=kind, check_profile=True)


def manifest_runtime_support(manifest: PluginManifest) -> dict[str, Any]:
    return get_runtime_registry().support(
        manifest.execution_mode,
        profile=manifest.runtime.profile if manifest.runtime else None,
        kind=manifest.runtime.kind if manifest.runtime else None,
        check_profile=True,
    )


class UnsupportedPluginRuntime(PluginContractError):
    def __init__(self, support: dict[str, Any]) -> None:
        self.support = support
        super().__init__(f"{support['reason_code']}: {support['reason']}")


def require_supported_runtime(manifest: PluginManifest) -> RuntimeAdapter:
    registry = get_runtime_registry()
    support = registry.support(
        manifest.execution_mode,
        profile=manifest.runtime.profile if manifest.runtime else None,
        kind=manifest.runtime.kind if manifest.runtime else None,
        check_profile=True,
    )
    if not support["supported"]:
        raise UnsupportedPluginRuntime(support)
    adapter = registry.resolve(manifest.execution_mode)
    assert adapter is not None
    return adapter
