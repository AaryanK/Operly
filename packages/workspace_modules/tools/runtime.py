from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from packages.kernel.bootstrap import build_kernel_runtime
from packages.kernel.contracts import RuntimeRequest, RuntimeResponse
from packages.kernel.runtime_availability import AvailabilityAwareKernelRuntime
from packages.plugins.capability_source import installed_plugin_capability_source
from packages.security.execution_context import ExecutionContext
from packages.workspace_modules.tools import register_workspace_providers, workspace_capabilities


PLUGIN_RUNTIME_PROVIDER_ID = "operly.plugin_runtime"


class WorkspaceRuntime(AvailabilityAwareKernelRuntime):
    """Canonical Workspace runtime with request-local plugin composition.

    The object itself carries the stable built-in Workspace registry so synchronous
    callers may inspect native contracts. Every discovery or execution call composes a
    fresh runtime view, then loads only the active plugin contracts for that Workspace.
    This prevents a process-wide runtime from retaining another tenant's plugin version
    while preserving one Kernel policy/provider/approval/audit path for all tools.
    """

    async def _load_active_plugins(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
    ) -> None:
        if not context.workspace_id:
            return
        plugin_specs = await installed_plugin_capability_source.list(
            db,
            context=context,
        )
        existing = {spec.id: spec for spec in self.registry.all()}
        for spec in plugin_specs:
            current = existing.get(spec.id)
            if current is None:
                self.registry.register(spec)
                existing[spec.id] = spec
                continue
            if current.provider_id != PLUGIN_RUNTIME_PROVIDER_ID:
                raise RuntimeError(
                    f"Installed plugin capability collides with Operly capability: {spec.id}"
                )
            if current.version != spec.version:
                raise RuntimeError(
                    f"Duplicate active plugin capability has conflicting versions: {spec.id}"
                )

    async def _request_runtime(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
    ) -> "WorkspaceRuntime":
        runtime = _compose_workspace_runtime()
        await runtime._load_active_plugins(db, context=context)
        return runtime

    async def available_capabilities(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        query: str | None = None,
        limit: int = 50,
    ):
        runtime = await self._request_runtime(db, context=context)
        return await AvailabilityAwareKernelRuntime.available_capabilities(
            runtime,
            db,
            context=context,
            query=query,
            limit=limit,
        )

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        request: RuntimeRequest,
    ) -> RuntimeResponse:
        runtime = await self._request_runtime(db, context=context)
        return await AvailabilityAwareKernelRuntime.execute(
            runtime,
            db,
            context=context,
            request=request,
        )


def _compose_workspace_runtime() -> WorkspaceRuntime:
    """Build one isolated runtime view containing native Workspace providers."""
    from packages.plugins.sandbox_job_runtime import SandboxJobPluginRuntimeProvider

    base = build_kernel_runtime()
    runtime = WorkspaceRuntime(
        registry=base.registry,
        providers=base.providers,
        policy=base.policy,
        context_loader=base.context_loader,
        planner=base.planner,
    )
    for spec in workspace_capabilities():
        runtime.registry.register(spec)
    register_workspace_providers(runtime.providers)
    runtime.providers.register(
        PLUGIN_RUNTIME_PROVIDER_ID,
        SandboxJobPluginRuntimeProvider(),
    )
    return runtime


def build_workspace_runtime() -> WorkspaceRuntime:
    """Return the stable Workspace runtime facade.

    Concrete provider dependencies are imported only when composition is actually
    requested. Dynamic plugin contracts are never retained across requests/tenants.
    """
    return _compose_workspace_runtime()
