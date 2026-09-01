from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from packages.plugins.contracts import NetworkPolicy, ResourcePolicy


@dataclass(frozen=True, slots=True)
class PluginBuildRequest:
    tenant_id: str
    installation_id: str
    version_id: str
    manifest_digest: str
    source_artifact_id: str
    runtime_profile: str
    network_policy: NetworkPolicy
    resource_policy: ResourcePolicy
    dependency_lock_required: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PluginBuildResult:
    build_id: str
    status: str
    source_artifact_id: str
    output_artifact_id: str | None
    output_digest: str | None
    sbom_artifact_id: str | None
    logs_artifact_id: str | None
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PluginStartRequest:
    tenant_id: str
    installation_id: str
    version_id: str
    runtime_profile: str
    artifact_id: str
    artifact_digest: str
    network_policy: NetworkPolicy
    resource_policy: ResourcePolicy
    runtime_identity_id: str | None = None
    service_binding_ids: tuple[str, ...] = ()
    credential_binding_ids: tuple[str, ...] = ()
    environment_handles: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PluginRuntimeStatus:
    runtime_instance_id: str
    state: str
    health_state: str
    provider: str
    provider_reference: str | None = None
    endpoint_reference: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)


class PluginRuntimeController(Protocol):
    """Execution-plane boundary for user/generated plugin workloads.

    Implementations may target Railway Sandbox, a managed container host, static object
    hosting or a remote service. They never decide business authorization and they do
    not receive provider credentials directly. Kernel remains the invocation authority.
    """

    async def validate_build(self, request: PluginBuildRequest) -> PluginBuildResult:
        ...

    async def start(self, request: PluginStartRequest) -> PluginRuntimeStatus:
        ...

    async def status(self, runtime_instance_id: str) -> PluginRuntimeStatus:
        ...

    async def stop(self, runtime_instance_id: str) -> PluginRuntimeStatus:
        ...

    async def destroy(self, runtime_instance_id: str) -> None:
        ...
