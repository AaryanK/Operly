"""Concrete plugin runtime dispatch and immutable implementation registration.

Manifest vocabulary is historical syntax. Only complete registered adapters confer
support; configuration, health and authorization are checked by existing backends.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from inspect import iscoroutinefunction
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from packages.plugins.contracts import PluginExecutionMode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from packages.plugins.runtime_provider import PluginRuntimeProvider
    from packages.plugins.runtime_reconciler import PluginRuntimeReconciler, RuntimeReconciliationResult
    from packages.kernel.contracts import CapabilityExecutionResult, CapabilitySpec
    from packages.security.execution_context import ExecutionContext

# Historical vocabulary only: these entries never register executable support.
DECLARED_MODE_BY_KIND = {
    "remote": PluginExecutionMode.REMOTE_HTTP,
    "job": PluginExecutionMode.SANDBOX_JOB,
    "web": PluginExecutionMode.WEB_SERVICE,
    "worker": PluginExecutionMode.WORKER,
    "static": PluginExecutionMode.STATIC_SITE,
}
_DECLARED_PROFILES = {
    PluginExecutionMode.PLATFORM_NATIVE: (),
    PluginExecutionMode.REMOTE_HTTP: ("remote-http",),
    PluginExecutionMode.SANDBOX_JOB: ("sandbox-job",),
    PluginExecutionMode.WEB_SERVICE: ("node-web", "python-fastapi"),
    PluginExecutionMode.WORKER: ("worker",),
    PluginExecutionMode.STATIC_SITE: ("static-web", "react-vite"),
}


class RuntimeAdapter(ABC):
    execution_mode: PluginExecutionMode
    implementation_id: str
    compatible_pairs: tuple[tuple[str, str], ...]
    requirements: tuple[str, ...]
    requires_artifact: bool
    requires_fresh_health: bool

    def check_support(self, *, profile: str | None, kind: str | None) -> bool:
        return (profile, kind) in self.compatible_pairs

    @abstractmethod
    async def reconcile(self, reconciler: PluginRuntimeReconciler, db: AsyncSession, *,
                        tenant_id: str, installation_id: str, endpoint: str | None) -> RuntimeReconciliationResult:
        ...

    @abstractmethod
    async def execute(self, provider: PluginRuntimeProvider, db: AsyncSession, *,
                      context: ExecutionContext, capability: CapabilitySpec,
                      arguments: dict[str, Any], minimum_context: dict[str, Any]) -> CapabilityExecutionResult:
        ...

    @abstractmethod
    def instance_available(self, instance: Any) -> bool:
        ...


@dataclass(frozen=True)
class RemoteHttpRuntimeAdapter(RuntimeAdapter):
    execution_mode: PluginExecutionMode = PluginExecutionMode.REMOTE_HTTP
    implementation_id: str = "operly.remote_http"
    compatible_pairs: tuple[tuple[str, str], ...] = (("remote-http", "remote"),)
    requirements: tuple[str, ...] = ("A separately operated HTTPS endpoint and successful health reconciliation are required.",)
    requires_artifact: bool = False
    requires_fresh_health: bool = True

    async def reconcile(self, reconciler, db, *, tenant_id, installation_id, endpoint):
        return await reconciler.reconcile_remote_http(
            db, tenant_id=tenant_id, installation_id=installation_id, endpoint=endpoint)

    async def execute(self, provider, db, **kwargs):
        return await provider._execute_remote_http(db, **kwargs)

    def instance_available(self, instance):
        # The common resolver already enforces healthy state and fresh heartbeat.
        return True


@dataclass(frozen=True)
class SandboxJobRuntimeAdapter(RuntimeAdapter):
    execution_mode: PluginExecutionMode = PluginExecutionMode.SANDBOX_JOB
    implementation_id: str = "operly.sandbox_job"
    compatible_pairs: tuple[tuple[str, str], ...] = (("sandbox-job", "job"),)
    requirements: tuple[str, ...] = ("Requires the Sandbox Runner and a validated immutable artifact; network access, credentials and capability bindings are not supported.",)
    requires_artifact: bool = True
    requires_fresh_health: bool = False

    async def reconcile(self, reconciler, db, *, tenant_id, installation_id, endpoint):
        return await reconciler.reconcile_sandbox_job(
            db, tenant_id=tenant_id, installation_id=installation_id)

    async def execute(self, provider, db, **kwargs):
        backend = provider._sandbox_backend()
        async with backend._execution_gate:
            return await backend._execute_sandbox_job(db, **kwargs)

    def instance_available(self, instance):
        return bool(instance.provider == "railway-sandbox-job" and instance.artifact_id
                    and instance.state == "ready" and instance.health_state == "healthy")


@dataclass(frozen=True, init=False)
class RuntimeAdapterRegistry:
    """Construct once from complete adapters; no mutable registration API."""

    _by_mode: Mapping[PluginExecutionMode, RuntimeAdapter]

    def __init__(self, adapters: Iterable[RuntimeAdapter]):
        by_mode = {}
        for adapter in adapters:
            if not isinstance(adapter, RuntimeAdapter) or not all(
                iscoroutinefunction(getattr(adapter, name, None)) for name in ("execute", "reconcile")
            ):
                raise ValueError("Runtime registration requires execution and reconciliation implementations")
            mode = adapter.execution_mode
            if not isinstance(mode, PluginExecutionMode) or mode is PluginExecutionMode.PLATFORM_NATIVE:
                raise ValueError("platform_native is reserved for trusted platform code")
            if mode in by_mode:
                raise ValueError(f"Duplicate runtime adapter: {mode.value}")
            if not adapter.implementation_id or not adapter.compatible_pairs or any(
                len(pair) != 2 or not all(isinstance(value, str) and value for value in pair)
                for pair in adapter.compatible_pairs
            ):
                raise ValueError("Runtime adapters require an identity and compatible profile/kind pairs")
            by_mode[mode] = adapter
        object.__setattr__(self, "_by_mode", MappingProxyType(by_mode))

    def resolve(self, mode: PluginExecutionMode) -> RuntimeAdapter | None:
        return self._by_mode.get(mode)

    def support(self, mode: PluginExecutionMode, *, profile: str | None = None,
                kind: str | None = None, check_profile: bool = False) -> dict[str, Any]:
        adapter = self.resolve(mode)
        code = reason = None
        if mode is PluginExecutionMode.PLATFORM_NATIVE:
            code = "platform_native_reserved"
            reason = "platform_native providers ship with trusted Operly code; Workspace plugins cannot provision them."
        elif adapter is None:
            code = "unsupported_runtime_mode"
            reason = f"Operly has no registered runtime adapter for {mode.value}. Historical manifests remain readable."
        elif check_profile and not adapter.check_support(profile=profile, kind=kind):
            code = "runtime_profile_mode_mismatch"
            reason = f"Runtime profile/kind does not match the {mode.value} adapter."
        return {
            "execution_mode": mode.value,
            "supported": adapter is not None and code is None,
            "reason_code": code,
            "reason": reason,
            "profiles": sorted({pair[0] for pair in adapter.compatible_pairs}) if adapter else list(_DECLARED_PROFILES[mode]),
            "requirements": list(adapter.requirements) if adapter else [],
            "implementation_id": adapter.implementation_id if adapter else None,
        }


_RUNTIME_REGISTRY = RuntimeAdapterRegistry((RemoteHttpRuntimeAdapter(), SandboxJobRuntimeAdapter()))


def get_runtime_registry() -> RuntimeAdapterRegistry:
    return _RUNTIME_REGISTRY
