from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ToolManifest:
    id: str
    required_permissions: tuple[str, ...] = ()
    risk_level: str = "read_only"
    approval_policy: str = "auto"
    resource_type: str | None = None
    mcp_default_exposed: bool = False


@dataclass(frozen=True, slots=True)
class PluginManifest:
    id: str
    version: str
    tools: tuple[ToolManifest, ...] = ()
    permissions: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    events: tuple[str, ...] = ()
    workflows: tuple[str, ...] = ()
    connectors: tuple[str, ...] = ()
    ui: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def tool_ids(self) -> set[str]:
        return {tool.id for tool in self.tools}


class PluginManifestRegistry:
    """Canonical manifest registry for Operly plugins.

    Business tools belong to Operly. Models, Discord, MCP, workflows and API clients
    are consumers of the same registered capabilities; they do not define duplicate
    client-specific tool implementations.
    """

    def __init__(self, manifests: Iterable[PluginManifest] = ()) -> None:
        self._manifests: dict[str, PluginManifest] = {}
        self._tool_owner: dict[str, str] = {}
        for manifest in manifests:
            self.register(manifest)

    def register(self, manifest: PluginManifest) -> None:
        if manifest.id in self._manifests:
            raise ValueError(f"Plugin already registered: {manifest.id}")
        for tool in manifest.tools:
            owner = self._tool_owner.get(tool.id)
            if owner:
                raise ValueError(f"Tool {tool.id} is already provided by {owner}")
        self._manifests[manifest.id] = manifest
        for tool in manifest.tools:
            self._tool_owner[tool.id] = manifest.id

    def manifest(self, plugin_id: str) -> PluginManifest:
        try:
            return self._manifests[plugin_id]
        except KeyError as error:
            raise LookupError(f"Unknown plugin: {plugin_id}") from error

    def owner_for_tool(self, tool_id: str) -> str | None:
        return self._tool_owner.get(tool_id)

    def manifests(self) -> tuple[PluginManifest, ...]:
        return tuple(self._manifests.values())
