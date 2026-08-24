"""Manifest-driven plugin contribution runtime.

During migration built-ins may still be bootstrapped by ``default_registry``. New
plugin code registers resources here so capabilities/models/runtimes can be composed
without editing agent or Studio code.
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
    task_delivery_adapters: tuple[Any, ...] = ()


class PluginRuntime:
    """Process-local registry for installed plugin contributions.

    This object owns registration/lifecycle composition only. Capability execution
    still goes through CapabilityRegistry + CapabilityFirewall, model execution goes
    through model_runtime, and generated code execution goes through RuntimeRegistry
    + the isolated runner. Registering a plugin is never an execution bypass.
    """

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

        # Model provider/discovery plugins own their own provider-runtime
        # registration. The harness never learns vendor names.
        for registrar in contribution.model_provider_registrars:
            registrar()
        for registrar in contribution.model_discoverer_registrars:
            registrar()

        # Runtime plugins join the canonical runtime registry immediately. This is
        # trusted execution metadata, not model-authored shell configuration.
        if contribution.runtime_plugins:
            from packages.runtime_plugins.registry import default_runtime_registry

            runtime_registry = default_runtime_registry()
            for runtime in contribution.runtime_plugins:
                runtime_registry.register(runtime, replace=replace)

    def contribution(self, plugin_id: str) -> PluginContribution:
        try:
            return self._contributions[plugin_id]
        except KeyError as error:
            raise LookupError(f"Unknown plugin: {plugin_id}") from error

    def contributions(self) -> tuple[PluginContribution, ...]:
        return tuple(self._contributions.values())

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

    def task_delivery_adapters(self) -> tuple[Any, ...]:
        return tuple(
            adapter
            for contribution in self._contributions.values()
            for adapter in contribution.task_delivery_adapters
        )

    def task_delivery_adapter(self, provider: str) -> Any | None:
        needle = str(provider or "").strip().lower()
        if not needle:
            return None
        matches: list[Any] = []
        for adapter in self.task_delivery_adapters():
            providers = tuple(
                str(item).strip().lower()
                for item in (getattr(adapter, "providers", ()) or ())
            )
            single = str(getattr(adapter, "provider", "") or "").strip().lower()
            if needle == single or needle in providers:
                matches.append(adapter)
        if len(matches) > 1:
            raise RuntimeError(f"Multiple task delivery adapters registered for {needle}")
        return matches[0] if matches else None

    async def install(self, plugin_id: str, context: Any = None) -> None:
        contribution = self.contribution(plugin_id)
        if contribution.lifecycle is not None:
            await contribution.lifecycle.install(context)

    async def uninstall(self, plugin_id: str, context: Any = None) -> None:
        contribution = self.contribution(plugin_id)
        if plugin_id in self._started and contribution.lifecycle is not None:
            await contribution.lifecycle.stop(context)
            self._started.discard(plugin_id)
        if contribution.lifecycle is not None:
            await contribution.lifecycle.uninstall(context)

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
            *(
                one(plugin_id, contribution)
                for plugin_id, contribution in self._contributions.items()
            )
        )
        return dict(rows)


_DEFAULT_PLUGIN_RUNTIME: PluginRuntime | None = None


def default_plugin_runtime() -> PluginRuntime:
    global _DEFAULT_PLUGIN_RUNTIME
    if _DEFAULT_PLUGIN_RUNTIME is None:
        _DEFAULT_PLUGIN_RUNTIME = PluginRuntime()
    return _DEFAULT_PLUGIN_RUNTIME
