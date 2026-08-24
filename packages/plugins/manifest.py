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
    """A durable event a plugin may emit for Task triggers.

    Event declarations are metadata only. Declaring an event never grants a plugin
    execution authority and never lets the model fabricate the event. A provider or
    connector must durably emit the event through the appropriate Operly event store.
    """

    id: str
    description: str = ""
    payload_schema: dict[str, Any] = field(default_factory=dict)
    scope: str = "workspace"  # workspace | personal | either
    tags: frozenset[str] = frozenset()


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

    def event_specs(self) -> tuple[EventSpec, ...]:
        output: list[EventSpec] = []
        for event in self.events:
            if isinstance(event, str):
                event_id = event.strip()
                if event_id:
                    output.append(EventSpec(event_id))
                continue
            event_id = str(event.id or "").strip()
            if event_id:
                output.append(event)
        return tuple(output)

    def event_ids(self) -> set[str]:
        return {event.id for event in self.event_specs()}

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

    This registry records who owns capabilities/events/resources. Execution continues
    through the capability registry/firewall; a manifest is never an execution bypass.
    """

    def __init__(self, manifests: Iterable[PluginManifest] = ()) -> None:
        self._manifests: dict[str, PluginManifest] = {}
        self._capability_owner: dict[str, str] = {}
        self._event_owner: dict[str, str] = {}
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
            for event_id in previous.event_ids():
                if self._event_owner.get(event_id) == manifest.id:
                    self._event_owner.pop(event_id, None)

        for capability_id in manifest.capability_ids():
            owner = self._capability_owner.get(capability_id)
            if owner and owner != manifest.id:
                raise ValueError(
                    f"Capability {capability_id} is already provided by {owner}"
                )
        for event_id in manifest.event_ids():
            owner = self._event_owner.get(event_id)
            if owner and owner != manifest.id:
                raise ValueError(f"Event {event_id} is already provided by {owner}")

        self._manifests[manifest.id] = manifest
        for capability_id in manifest.capability_ids():
            self._capability_owner[capability_id] = manifest.id
        for event_id in manifest.event_ids():
            self._event_owner[event_id] = manifest.id

    def manifest(self, plugin_id: str) -> PluginManifest:
        try:
            return self._manifests[plugin_id]
        except KeyError as error:
            raise LookupError(f"Unknown plugin: {plugin_id}") from error

    def owner_for_capability(self, capability_id: str) -> str | None:
        return self._capability_owner.get(capability_id)

    def owner_for_event(self, event_id: str) -> str | None:
        return self._event_owner.get(event_id)

    def event(self, event_id: str) -> EventSpec:
        owner = self.owner_for_event(event_id)
        if owner is None:
            raise LookupError(f"Unknown plugin event: {event_id}")
        for event in self.manifest(owner).event_specs():
            if event.id == event_id:
                return event
        raise LookupError(f"Unknown plugin event: {event_id}")

    def events(self) -> tuple[tuple[str, EventSpec], ...]:
        rows: list[tuple[str, EventSpec]] = []
        for plugin_id, manifest in self._manifests.items():
            rows.extend((plugin_id, event) for event in manifest.event_specs())
        return tuple(rows)

    def owner_for_tool(self, tool_id: str) -> str | None:
        return self.owner_for_capability(tool_id)

    def manifests(self) -> tuple[PluginManifest, ...]:
        return tuple(self._manifests.values())
