"""Internal runtime plugin registry for Operly harness extension points.

Capabilities are the model-visible plugin surface. This registry covers application-
controlled extension points that must remain outside the model tool list, such as
routing and attachment ingestion. Those plugins can prepare or select work, but
cannot bypass the canonical capability/firewall boundary for side effects.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Protocol


class RuntimePluginUnavailable(RuntimeError):
    """A runtime plugin cannot serve this invocation; another plugin may try."""


@dataclass(frozen=True, slots=True)
class RuntimePluginContext:
    channel: str = ""
    surface: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class RuntimePlugin(Protocol):
    id: str
    kind: str
    priority: int

    def supports(self, payload: dict[str, Any], context: RuntimePluginContext) -> bool: ...

    async def invoke(
        self,
        payload: dict[str, Any],
        context: RuntimePluginContext,
    ) -> Any: ...


class RuntimePluginRegistry:
    """Ordered registry for application-controlled harness plugins.

    A plugin may decline by returning ``False`` from ``supports`` or by raising
    ``RuntimePluginUnavailable``. Other exceptions are treated as implementation
    faults and are intentionally not swallowed.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, RuntimePlugin] = {}

    def register(self, plugin: RuntimePlugin, *, replace: bool = False) -> None:
        plugin_id = str(getattr(plugin, "id", "")).strip()
        kind = str(getattr(plugin, "kind", "")).strip()
        if not plugin_id or not kind:
            raise ValueError("Runtime plugins require non-empty id and kind")
        if plugin_id in self._plugins and not replace:
            raise ValueError(f"Runtime plugin already registered: {plugin_id}")
        self._plugins[plugin_id] = plugin

    def installed(self, kind: str | None = None) -> tuple[RuntimePlugin, ...]:
        rows = list(self._plugins.values())
        if kind:
            rows = [row for row in rows if row.kind == kind]
        rows.sort(key=lambda row: (int(getattr(row, "priority", 100)), row.id))
        return tuple(rows)

    async def invoke(
        self,
        kind: str,
        payload: dict[str, Any],
        context: RuntimePluginContext | None = None,
    ) -> Any:
        runtime_context = context or RuntimePluginContext()
        declined: list[str] = []
        for plugin in self.installed(kind):
            try:
                supported = plugin.supports(payload, runtime_context)
                if inspect.isawaitable(supported):
                    supported = await supported
                if not supported:
                    continue
                result = plugin.invoke(payload, runtime_context)
                return await result if inspect.isawaitable(result) else result
            except RuntimePluginUnavailable as error:
                declined.append(f"{plugin.id}: {str(error)[:200]}")
                continue
        detail = "; ".join(declined)
        raise RuntimePluginUnavailable(
            f"No runtime plugin handled kind={kind}" + (f" ({detail})" if detail else "")
        )


_DEFAULT_RUNTIME_PLUGINS = RuntimePluginRegistry()


def default_runtime_plugins() -> RuntimePluginRegistry:
    return _DEFAULT_RUNTIME_PLUGINS
