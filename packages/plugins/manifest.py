from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    id: str
    description: str = ""
    risk: str = "standard"


@dataclass(frozen=True, slots=True)
class EventSpec:
    id: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class PluginLifecycleSpec:
    start_on_boot: bool = False
    supports_health: bool = False
    supports_install: bool = False
    supports_uninstall: bool = False


@dataclass(frozen=True, slots=True)
class ToolManifest:
    """Compatibility metadata for callers that still speak in tools."""

    id: str
    required_permissions: tuple[str, ...] = ()
    risk_level: str = "read_only"
    approval_policy: str = "auto"
    resource_type: str | None = None
    mcp_default_exposed: bool = False


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Canonical public manifest for an installable Operly plugin.

    The typed resource tuples deliberately accept structural objects rather than
    importing their implementations here. This keeps the plugin manifest layer
    above vendor code and avoids circular dependencies with capabilities/models/
    runtimes while still making one package the declared owner of resources.
    """

    id: str
    version: str
    display_name: str = ""
    description: str = ""

    capabilities: tuple[Any, ...] = ()
    permissions: tuple[PermissionSpec | str, ...] = ()
    model_providers: tuple[Any, ...] = ()
    model_discoverers: tuple[Any, ...] = ()
    runtime_plugins: tuple[Any, ...] = ()
    lifecycle: PluginLifecycleSpec | None = None
    events: tuple[EventSpec | str, ...] = ()
    configuration_schema: dict[str, Any] | None = None

    # Migration-only fields used by existing manifest consumers.
    tools: tuple[ToolManifest, ...] = ()
    resources: tuple[str, ...] = ()
    workflows: tuple[str, ...] = ()
    connectors: tuple[str, ...] = ()
    ui: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def capability_ids(self) -> set[str]:
        ids = {str(getattr(item, "id", "")).strip() for item in self.capabilities}
        ids.discard("")
        ids.update(tool.id for tool in self.tools)
        return ids

    def tool_ids(self) -> set[str]:
        return self.capability_ids()

    def permission_ids(self) -> set[str]:
        output: set[str] = set()
        for permission in self.permissions:
            if isinstance(permission, str):
                value = permission.strip()
            else:
                value = permission.id.strip()
            if value:
                output.add(value)
        return output


class PluginManifestRegistry:
    """Manifest registry and resource-ownership index.

    This registry records who owns capabilities/resources. Execution continues
    through the capability registry/firewall; a manifest is never an execution
    bypass.
    """

    def __init__(self, manifests: Iterable[PluginManifest] = ()) -> None:
        self._manifests: dict[str, PluginManifest] = {}
        self._capability_owner: dict[str, str] = {}
        for manifest in manifests:
            self.register(manifest)

    def register(self, manifest: PluginManifest, *, replace: bool = False) -> None:
        if manifest.id in self._manifests and not replace:
            raise ValueError(f"Plugin already registered: {manifest.id}")

        if replace and manifest.id in self._manifests:
            previous = self._manifests[manifest.id]
            for capability_id in previous.capability_ids():
                if self._capability_owner.get(capability_id) == manifest.id:
                    self._capability_owner.pop(capability_id, None)

        for capability_id in manifest.capability_ids():
            owner = self._capability_owner.get(capability_id)
            if owner and owner != manifest.id:
                raise ValueError(
                    f"Capability {capability_id} is already provided by {owner}"
                )

        self._manifests[manifest.id] = manifest
        for capability_id in manifest.capability_ids():
            self._capability_owner[capability_id] = manifest.id

    def manifest(self, plugin_id: str) -> PluginManifest:
        try:
            return self._manifests[plugin_id]
        except KeyError as error:
            raise LookupError(f"Unknown plugin: {plugin_id}") from error

    def owner_for_capability(self, capability_id: str) -> str | None:
        return self._capability_owner.get(capability_id)

    def owner_for_tool(self, tool_id: str) -> str | None:
        return self.owner_for_capability(tool_id)

    def manifests(self) -> tuple[PluginManifest, ...]:
        return tuple(self._manifests.values())
