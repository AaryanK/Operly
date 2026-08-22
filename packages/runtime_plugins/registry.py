"""Registry for trusted software runtime plugins."""
from __future__ import annotations

from typing import Any, Iterable

from packages.runtime_plugins.contracts import RuntimePlugin


class RuntimeRegistry:
    def __init__(self, plugins: Iterable[RuntimePlugin] = ()) -> None:
        self._plugins: dict[str, RuntimePlugin] = {}
        for plugin in plugins:
            self.register(plugin)

    def register(self, plugin: RuntimePlugin, *, replace: bool = False) -> None:
        runtime_id = plugin.spec.id
        if runtime_id in self._plugins and not replace:
            raise ValueError(f"Runtime already registered: {runtime_id}")
        self._plugins[runtime_id] = plugin

    def get(self, runtime_id: str) -> RuntimePlugin:
        try:
            return self._plugins[runtime_id]
        except KeyError as error:
            raise LookupError(f"Unknown runtime: {runtime_id}") from error

    def plugins(self) -> tuple[RuntimePlugin, ...]:
        return tuple(self._plugins.values())

    def resolve(self, source: Any, requirements: Any = None) -> RuntimePlugin:
        del requirements  # reserved for requirement-aware policy ranking
        matches = []
        for plugin in self._plugins.values():
            match = plugin.detect(source)
            if match.matched:
                matches.append((match.score, plugin.spec.id, plugin))
        if not matches:
            raise LookupError(
                "No isolated runner profile matches the source tree; "
                "no installed runtime plugin recognized this source shape"
            )
        matches.sort(key=lambda item: (-item[0], item[1]))
        selected = matches[0][2]
        validation = selected.validate(source)
        if not validation.valid:
            raise ValueError(
                f"Runtime {selected.spec.id} rejected source: "
                + "; ".join(validation.errors)
            )
        return selected


_DEFAULT_RUNTIME_REGISTRY: RuntimeRegistry | None = None


def default_runtime_registry() -> RuntimeRegistry:
    global _DEFAULT_RUNTIME_REGISTRY
    if _DEFAULT_RUNTIME_REGISTRY is None:
        _DEFAULT_RUNTIME_REGISTRY = RuntimeRegistry()
    return _DEFAULT_RUNTIME_REGISTRY
