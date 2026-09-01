from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from packages.kernel.contracts import CapabilityRisk, CapabilitySpec

_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,119}$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9_.-]+)?$")


class PluginContractError(ValueError):
    pass


class PluginExecutionMode(StrEnum):
    PLATFORM_NATIVE = "platform_native"
    REMOTE_HTTP = "remote_http"
    SANDBOX_JOB = "sandbox_job"
    WEB_SERVICE = "web_service"
    WORKER = "worker"
    STATIC_SITE = "static_site"


class PluginLifecycleState(StrEnum):
    PROPOSED = "proposed"
    VALIDATING = "validating"
    INSTALLED = "installed"
    CONFIGURING = "configuring"
    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    FAILED = "failed"
    UNINSTALLING = "uninstalling"
    UNINSTALLED = "uninstalled"


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    cpu_millicores: int = 500
    memory_mb: int = 512
    disk_mb: int = 1024
    max_runtime_seconds: int = 300
    max_concurrency: int = 1

    def validate(self) -> None:
        if not 50 <= self.cpu_millicores <= 8000:
            raise PluginContractError("cpu_millicores must be between 50 and 8000")
        if not 64 <= self.memory_mb <= 32768:
            raise PluginContractError("memory_mb must be between 64 and 32768")
        if not 64 <= self.disk_mb <= 102400:
            raise PluginContractError("disk_mb must be between 64 and 102400")
        if not 1 <= self.max_runtime_seconds <= 86400:
            raise PluginContractError("max_runtime_seconds must be between 1 and 86400")
        if not 1 <= self.max_concurrency <= 100:
            raise PluginContractError("max_concurrency must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    mode: str = "off"
    allowed_hosts: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.mode not in {"off", "egress"}:
            raise PluginContractError("network mode must be off or egress")
        if self.mode == "off" and self.allowed_hosts:
            raise PluginContractError("allowed_hosts requires egress network mode")
        for host in self.allowed_hosts:
            normalized = str(host or "").strip().lower()
            if not normalized or "/" in normalized or "://" in normalized or " " in normalized:
                raise PluginContractError("allowed_hosts entries must be hostnames, not URLs")


@dataclass(frozen=True, slots=True)
class RuntimeRequirement:
    profile: str
    kind: str
    network: NetworkPolicy = field(default_factory=NetworkPolicy)
    resources: ResourcePolicy = field(default_factory=ResourcePolicy)
    exposed_port: int | None = None
    health_path: str | None = None

    def validate(self) -> None:
        if not _ID_RE.fullmatch(self.profile):
            raise PluginContractError("runtime profile must be a normalized ID")
        if self.kind not in {"job", "web", "worker", "static", "remote"}:
            raise PluginContractError("runtime kind is unsupported")
        self.network.validate()
        self.resources.validate()
        if self.exposed_port is not None and not 1 <= self.exposed_port <= 65535:
            raise PluginContractError("exposed_port is invalid")
        if self.health_path is not None and not str(self.health_path).startswith("/"):
            raise PluginContractError("health_path must begin with /")


@dataclass(frozen=True, slots=True)
class StorageRequest:
    name: str
    kind: str = "kv"
    quota_bytes: int = 10 * 1024 * 1024

    def validate(self) -> None:
        if not _ID_RE.fullmatch(self.name):
            raise PluginContractError("storage name must be a normalized ID")
        if self.kind not in {"kv", "document", "blob"}:
            raise PluginContractError("storage kind must be kv, document, or blob")
        if not 1024 <= self.quota_bytes <= 100 * 1024 * 1024 * 1024:
            raise PluginContractError("storage quota is outside platform limits")


@dataclass(frozen=True, slots=True)
class CredentialRequest:
    """A declared credential handle; raw secret values never appear in a manifest."""

    name: str
    credential_type: str
    required: bool = True
    scopes: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ()
    description: str = ""

    def validate(self) -> None:
        if not _ID_RE.fullmatch(self.name):
            raise PluginContractError("credential name must be a normalized ID")
        if self.credential_type not in {"api_key", "bearer", "basic", "oauth2", "custom"}:
            raise PluginContractError("unsupported credential_type")
        for host in self.allowed_hosts:
            normalized = str(host or "").strip().lower()
            if not normalized or "/" in normalized or "://" in normalized or " " in normalized:
                raise PluginContractError("credential allowed_hosts entries must be hostnames")
        if len(self.scopes) > 100:
            raise PluginContractError("credential request has too many scopes")


@dataclass(frozen=True, slots=True)
class EventDeclaration:
    name: str
    description: str = ""
    schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})

    def validate(self) -> None:
        if not _ID_RE.fullmatch(self.name):
            raise PluginContractError("event name must be a normalized ID")
        if self.schema.get("type") != "object":
            raise PluginContractError("event schema must be an object schema")


@dataclass(frozen=True, slots=True)
class BindingRequest:
    semantic_name: str
    capability_query: str
    required: bool = True

    def validate(self) -> None:
        if not _ID_RE.fullmatch(self.semantic_name):
            raise PluginContractError("binding semantic_name must be a normalized ID")
        if not str(self.capability_query or "").strip():
            raise PluginContractError("binding capability_query is required")


@dataclass(frozen=True, slots=True)
class UIContribution:
    contribution_type: str
    id: str
    title: str
    configuration: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.contribution_type not in {"navigation", "card", "table", "form", "settings", "iframe"}:
            raise PluginContractError("unsupported UI contribution type")
        if not _ID_RE.fullmatch(self.id):
            raise PluginContractError("UI contribution ID must be normalized")
        if not str(self.title or "").strip():
            raise PluginContractError("UI contribution title is required")


@dataclass(frozen=True, slots=True)
class PluginCapability:
    id: str
    display_name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    permissions: tuple[str, ...] = ()
    risk: CapabilityRisk = CapabilityRisk.READ_ONLY
    approval_required: bool = False
    reversible: bool = False
    aliases: tuple[str, ...] = ()
    emits: tuple[str, ...] = ()
    tags: frozenset[str] = frozenset()

    def validate(self) -> None:
        if not _ID_RE.fullmatch(self.id):
            raise PluginContractError(f"invalid capability ID: {self.id}")
        if not self.display_name.strip() or not self.description.strip():
            raise PluginContractError(f"capability {self.id} requires display_name and description")
        if self.input_schema.get("type") != "object":
            raise PluginContractError(f"capability {self.id} input_schema must be an object schema")
        if self.output_schema.get("type") != "object":
            raise PluginContractError(f"capability {self.id} output_schema must be an object schema")

    def to_kernel_spec(
        self,
        *,
        version: str,
        provider_id: str = "operly.plugin_runtime",
    ) -> CapabilitySpec:
        self.validate()
        return CapabilitySpec(
            id=self.id,
            version=version,
            display_name=self.display_name,
            description=self.description,
            provider_id=provider_id,
            scopes=frozenset({"workspace"}),
            input_schema=dict(self.input_schema),
            output_schema=dict(self.output_schema),
            permissions=self.permissions,
            risk=self.risk,
            approval_required=self.approval_required,
            resource_scope="workspace",
            reversible=self.reversible,
            aliases=self.aliases,
            emits=self.emits,
            tags=self.tags | frozenset({"plugin"}),
        )


@dataclass(frozen=True, slots=True)
class PluginManifest:
    schema_version: str
    plugin_id: str
    version: str
    display_name: str
    description: str
    execution_mode: PluginExecutionMode
    capabilities: tuple[PluginCapability, ...] = ()
    permissions: tuple[str, ...] = ()
    configuration_schema: Mapping[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False}
    )
    runtime: RuntimeRequirement | None = None
    storage: tuple[StorageRequest, ...] = ()
    credentials: tuple[CredentialRequest, ...] = ()
    produces_events: tuple[EventDeclaration, ...] = ()
    consumes_events: tuple[str, ...] = ()
    requested_bindings: tuple[BindingRequest, ...] = ()
    ui: tuple[UIContribution, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.schema_version != "operly.plugin/v1":
            raise PluginContractError("unsupported plugin manifest schema_version")
        if not _ID_RE.fullmatch(self.plugin_id):
            raise PluginContractError("plugin_id must be a normalized lowercase ID")
        if not _VERSION_RE.fullmatch(self.version):
            raise PluginContractError("plugin version must use semantic x.y.z form")
        if not self.display_name.strip() or not self.description.strip():
            raise PluginContractError("plugin display_name and description are required")
        if self.configuration_schema.get("type") != "object":
            raise PluginContractError("configuration_schema must be an object schema")
        if len({item.id for item in self.capabilities}) != len(self.capabilities):
            raise PluginContractError("plugin capability IDs must be unique")
        if len({item.name for item in self.storage}) != len(self.storage):
            raise PluginContractError("plugin storage names must be unique")
        if len({item.name for item in self.credentials}) != len(self.credentials):
            raise PluginContractError("plugin credential request names must be unique")
        for item in self.capabilities:
            item.validate()
        for item in self.storage:
            item.validate()
        for item in self.credentials:
            item.validate()
        for item in self.produces_events:
            item.validate()
        for item in self.requested_bindings:
            item.validate()
        for item in self.ui:
            item.validate()
        if self.runtime:
            self.runtime.validate()
            runtime_hosts = set(self.runtime.network.allowed_hosts)
            for credential in self.credentials:
                if credential.allowed_hosts and not set(credential.allowed_hosts).issubset(runtime_hosts):
                    raise PluginContractError(
                        f"credential {credential.name} cannot authorize hosts outside the runtime egress allowlist"
                    )
        if self.execution_mode is PluginExecutionMode.PLATFORM_NATIVE and self.runtime is not None:
            raise PluginContractError("platform_native plugins do not declare an untrusted runtime")
        if self.execution_mode is not PluginExecutionMode.PLATFORM_NATIVE and self.runtime is None:
            raise PluginContractError("non-native plugins must declare a runtime requirement")

    def capability_specs(self) -> tuple[CapabilitySpec, ...]:
        self.validate()
        return tuple(item.to_kernel_spec(version=self.version) for item in self.capabilities)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["execution_mode"] = self.execution_mode.value
        for capability in value["capabilities"]:
            risk = capability.get("risk")
            capability["risk"] = risk.value if isinstance(risk, CapabilityRisk) else str(risk)
            capability["tags"] = sorted(capability.get("tags") or [])
        return value

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PluginManifest":
        if not isinstance(raw, Mapping):
            raise PluginContractError("plugin manifest must be an object")

        def tuple_of(key: str) -> tuple[Any, ...]:
            value = raw.get(key) or []
            if not isinstance(value, list):
                raise PluginContractError(f"{key} must be an array")
            return tuple(value)

        def object_item(value: Any, *, label: str) -> Mapping[str, Any]:
            if not isinstance(value, Mapping):
                raise PluginContractError(f"{label} entries must be objects")
            return value

        capabilities = tuple(
            PluginCapability(
                id=str(item["id"]),
                display_name=str(item["display_name"]),
                description=str(item["description"]),
                input_schema=dict(item.get("input_schema") or {"type": "object", "properties": {}}),
                output_schema=dict(item.get("output_schema") or {"type": "object", "properties": {}}),
                permissions=tuple(str(v) for v in item.get("permissions") or []),
                risk=CapabilityRisk(str(item.get("risk") or "read_only")),
                approval_required=bool(item.get("approval_required", False)),
                reversible=bool(item.get("reversible", False)),
                aliases=tuple(str(v) for v in item.get("aliases") or []),
                emits=tuple(str(v) for v in item.get("emits") or []),
                tags=frozenset(str(v) for v in item.get("tags") or []),
            )
            for item in (object_item(v, label="capabilities") for v in tuple_of("capabilities"))
        )
        runtime_raw = raw.get("runtime")
        runtime = None
        if runtime_raw is not None:
            if not isinstance(runtime_raw, Mapping):
                raise PluginContractError("runtime must be an object")
            network_raw = runtime_raw.get("network") or {}
            resources_raw = runtime_raw.get("resources") or {}
            if not isinstance(network_raw, Mapping):
                raise PluginContractError("runtime.network must be an object")
            if not isinstance(resources_raw, Mapping):
                raise PluginContractError("runtime.resources must be an object")
            runtime = RuntimeRequirement(
                profile=str(runtime_raw.get("profile") or ""),
                kind=str(runtime_raw.get("kind") or ""),
                network=NetworkPolicy(
                    mode=str(network_raw.get("mode") or "off"),
                    allowed_hosts=tuple(str(v) for v in network_raw.get("allowed_hosts") or []),
                ),
                resources=ResourcePolicy(
                    cpu_millicores=int(resources_raw.get("cpu_millicores") or 500),
                    memory_mb=int(resources_raw.get("memory_mb") or 512),
                    disk_mb=int(resources_raw.get("disk_mb") or 1024),
                    max_runtime_seconds=int(resources_raw.get("max_runtime_seconds") or 300),
                    max_concurrency=int(resources_raw.get("max_concurrency") or 1),
                ),
                exposed_port=int(runtime_raw["exposed_port"]) if runtime_raw.get("exposed_port") is not None else None,
                health_path=str(runtime_raw["health_path"]) if runtime_raw.get("health_path") else None,
            )
        manifest = cls(
            schema_version=str(raw.get("schema_version") or ""),
            plugin_id=str(raw.get("plugin_id") or ""),
            version=str(raw.get("version") or ""),
            display_name=str(raw.get("display_name") or ""),
            description=str(raw.get("description") or ""),
            execution_mode=PluginExecutionMode(str(raw.get("execution_mode") or "")),
            capabilities=capabilities,
            permissions=tuple(str(v) for v in raw.get("permissions") or []),
            configuration_schema=dict(
                raw.get("configuration_schema")
                or {"type": "object", "properties": {}, "additionalProperties": False}
            ),
            runtime=runtime,
            storage=tuple(
                StorageRequest(
                    name=str(v["name"]),
                    kind=str(v.get("kind") or "kv"),
                    quota_bytes=int(v.get("quota_bytes") or 10 * 1024 * 1024),
                )
                for v in (object_item(v, label="storage") for v in tuple_of("storage"))
            ),
            credentials=tuple(
                CredentialRequest(
                    name=str(v["name"]),
                    credential_type=str(v.get("credential_type") or "custom"),
                    required=bool(v.get("required", True)),
                    scopes=tuple(str(scope) for scope in v.get("scopes") or []),
                    allowed_hosts=tuple(str(host) for host in v.get("allowed_hosts") or []),
                    description=str(v.get("description") or ""),
                )
                for v in (object_item(v, label="credentials") for v in tuple_of("credentials"))
            ),
            produces_events=tuple(
                EventDeclaration(
                    name=str(v["name"]),
                    description=str(v.get("description") or ""),
                    schema=dict(v.get("schema") or {"type": "object"}),
                )
                for v in (object_item(v, label="produces_events") for v in tuple_of("produces_events"))
            ),
            consumes_events=tuple(str(v) for v in raw.get("consumes_events") or []),
            requested_bindings=tuple(
                BindingRequest(
                    semantic_name=str(v["semantic_name"]),
                    capability_query=str(v["capability_query"]),
                    required=bool(v.get("required", True)),
                )
                for v in (object_item(v, label="requested_bindings") for v in tuple_of("requested_bindings"))
            ),
            ui=tuple(
                UIContribution(
                    contribution_type=str(v["contribution_type"]),
                    id=str(v["id"]),
                    title=str(v["title"]),
                    configuration=dict(v.get("configuration") or {}),
                )
                for v in (object_item(v, label="ui") for v in tuple_of("ui"))
            ),
            metadata=dict(raw.get("metadata") or {}),
        )
        manifest.validate()
        return manifest
