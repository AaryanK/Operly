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
    """Canonical Workspace runtime plus active installable plugin contracts.

    Plugin contracts are loaded from durable installation state into this request-local
    Kernel registry. They do not get a second authorization path: registry visibility,
    current Workspace permissions, approvals, idempotency, audit and provider execution
    remain the same Kernel stages as native Workspace capabilities.
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
                    f"Installed plugin capability version changed during one runtime view: {spec.id}"
                )

    async def available_capabilities(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        query: str | None = None,
        limit: int = 50,
    ):
        await self._load_active_plugins(db, context=context)
        return await super().available_capabilities(
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
        await self._load_active_plugins(db, context=context)
        return await super().execute(db, context=context, request=request)


def build_workspace_runtime() -> WorkspaceRuntime:
    """Compose the shared governed substrate with Workspace-owned tools.

    The generic Kernel package does not import Workspace modules. Workspace owns this
    composition root so its business/provider surface can evolve without turning the
    execution substrate into a second Workspace package. Concrete plugin transport
    dependencies are imported only when this composition root is actually built.
    """
    from packages.plugins.runtime_provider import PluginRuntimeProvider

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
    runtime.providers.register(PLUGIN_RUNTIME_PROVIDER_ID, PluginRuntimeProvider())
    return runtime
