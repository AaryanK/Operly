"""Application-controlled plugin extension points.

Capabilities are the model-visible plugin surface. This registry is for trusted
application extension points that stay outside the model tool list, such as task
routing and attachment ingestion. Extension plugins may prepare or select work,
but they cannot bypass the canonical capability/firewall boundary for side effects.

This primitive used to live under ``packages.harness`` even though it was not part
of the legacy agent harness. Keeping it in the canonical plugin package makes that
boundary explicit and allows the old harness package to be retired completely.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Protocol


class ApplicationPluginUnavailable(RuntimeError):
    """An application extension cannot serve this invocation; another may try."""


@dataclass(frozen=True, slots=True)
class ApplicationPluginContext:
    channel: str = ""
    surface: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ApplicationPlugin(Protocol):
    id: str
    kind: str
    priority: int

    def supports(
        self,
        payload: dict[str, Any],
        context: ApplicationPluginContext,
    ) -> bool: ...

    async def invoke(
        self,
        payload: dict[str, Any],
        context: ApplicationPluginContext,
    ) -> Any: ...


class ApplicationPluginRegistry:
    """Ordered registry for trusted, application-controlled extension points."""

    def __init__(self) -> None:
        self._plugins: dict[str, ApplicationPlugin] = {}

    def register(self, plugin: ApplicationPlugin, *, replace: bool = False) -> None:
        plugin_id = str(getattr(plugin, "id", "")).strip()
        kind = str(getattr(plugin, "kind", "")).strip()
        if not plugin_id or not kind:
            raise ValueError("Application plugins require non-empty id and kind")
        if plugin_id in self._plugins and not replace:
            raise ValueError(f"Application plugin already registered: {plugin_id}")
        self._plugins[plugin_id] = plugin

    def installed(self, kind: str | None = None) -> tuple[ApplicationPlugin, ...]:
        rows = list(self._plugins.values())
        if kind:
            rows = [row for row in rows if row.kind == kind]
        rows.sort(key=lambda row: (int(getattr(row, "priority", 100)), row.id))
        return tuple(rows)

    async def invoke(
        self,
        kind: str,
        payload: dict[str, Any],
        context: ApplicationPluginContext | None = None,
    ) -> Any:
        plugin_context = context or ApplicationPluginContext()
        declined: list[str] = []
        for plugin in self.installed(kind):
            try:
                supported = plugin.supports(payload, plugin_context)
                if inspect.isawaitable(supported):
                    supported = await supported
                if not supported:
                    continue
                result = plugin.invoke(payload, plugin_context)
                return await result if inspect.isawaitable(result) else result
            except ApplicationPluginUnavailable as error:
                declined.append(f"{plugin.id}: {str(error)[:200]}")
                continue
        detail = "; ".join(declined)
        raise ApplicationPluginUnavailable(
            f"No application plugin handled kind={kind}"
            + (f" ({detail})" if detail else "")
        )


_DEFAULT_APPLICATION_PLUGINS = ApplicationPluginRegistry()


def default_application_plugins() -> ApplicationPluginRegistry:
    return _DEFAULT_APPLICATION_PLUGINS


__all__ = [
    "ApplicationPlugin",
    "ApplicationPluginContext",
    "ApplicationPluginRegistry",
    "ApplicationPluginUnavailable",
    "default_application_plugins",
]
