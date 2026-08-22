"""Manifest-driven plugin contribution runtime.

During migration built-ins may still be bootstrapped by ``default_registry``. New
plugin code should register resources here so capabilities/models/runtimes can be
composed without editing agent or Studio code.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from packages.plugins.manifest import PluginManifest, PluginManifestRegistry


@dataclass(frozen=True, slots=True)
class PluginHealthResult:
    healthy: bool
    detail: str = ""
    metadata: dict[str, Any] | None = None


class PluginLifecycle(Protocol):
    async def install(self, context: Any) -> Any: ...
    async def start(self, context: Any) -> Any: ...
    async def health(self, context: Any) -> PluginHealthResult: ...
    async def stop(self, context: Any) -> Any: ...
    async def uninstall(self, context: Any) -> Any: ...


@dataclass(slots=True)
class PluginContribution:
    manifest: PluginManifest
    capability_provider: Any | None = None
    lifecycle: PluginLifecycle | None = None
    runtime_plugins: tuple[Any, ...] = ()
    model_provider_registrars: tuple[Any, ...] = ()
    model_discoverer_registrars: tuple[Any, ...] = ()


class PluginRuntime:
    def __init__(self) -> None:
        self.manifests = PluginManifestRegistry()
        self._contributions: dict[str, PluginContribution] = {}
        self._started: set[str] = set()

    def register(self, contribution: PluginContribution, *, replace: bool = False) -> None:
        plugin_id = contribution.manifest.id
        if plugin_id in self._contributions and not replace:
            raise ValueError(f"Plugin already registered: {plugin_id}")
        self.manifests.register(contribution.manifest, replace=replace)
        self._contributions[plugin_id] = contribution

        for registrar in contribution.model_provider_registrars:
            registrar()
        for registrar in contribution.model_discoverer_registrars:
            registrar()

    def contribution(self, plugin_id: str) -> PluginContribution:
        try:
            return self._contributions[plugin_id]
        except KeyError as error:
            raise LookupError(f"Unknown plugin: {plugin_id}") from error

    def capability_providers(self) -> tuple[Any, ...]:
        return tuple(
            contribution.capability_provider
            for contribution in self._contributions.values()
            if contribution.capability_provider is not None
        )

    def runtime_plugins(self) -> tuple[Any, ...]:
        return tuple(
            runtime
            for contribution in self._contributions.values()
            for runtime in contribution.runtime_plugins
        )

    async def start(self, context: Any = None) -> None:
        for plugin_id, contribution in self._contributions.items():
            if plugin_id in self._started or contribution.lifecycle is None:
                continue
            await contribution.lifecycle.start(context)
            self._started.add(plugin_id)

    async def stop(self, context: Any = None) -> None:
        for plugin_id in list(reversed(tuple(self._started))):
            contribution = self._contributions.get(plugin_id)
            if contribution and contribution.lifecycle is not None:
                try:
                    await contribution.lifecycle.stop(context)
                finally:
                    self._started.discard(plugin_id)

    async def health(self, context: Any = None) -> dict[str, PluginHealthResult]:
        async def one(plugin_id: str, contribution: PluginContribution):
            if contribution.lifecycle is None:
                return plugin_id, PluginHealthResult(True, "no lifecycle health hook")
            try:
                return plugin_id, await contribution.lifecycle.health(context)
            except Exception as error:
                return plugin_id, PluginHealthResult(False, type(error).__name__)

        rows = await asyncio.gather(
            *(one(plugin_id, contribution) for plugin_id, contribution in self._contributions.items())
        )
        return dict(rows)


_DEFAULT_PLUGIN_RUNTIME: PluginRuntime | None = None


def default_plugin_runtime() -> PluginRuntime:
    global _DEFAULT_PLUGIN_RUNTIME
    if _DEFAULT_PLUGIN_RUNTIME is None:
        _DEFAULT_PLUGIN_RUNTIME = PluginRuntime()
    return _DEFAULT_PLUGIN_RUNTIME
