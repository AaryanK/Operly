from __future__ import annotations

from packages.kernel.bootstrap import build_kernel_runtime
from packages.kernel.runtime_availability import AvailabilityAwareKernelRuntime
from packages.workspace_modules.tools import register_workspace_providers, workspace_capabilities


def build_workspace_runtime() -> AvailabilityAwareKernelRuntime:
    """Compose the shared governed substrate with Workspace-owned tools.

    The generic Kernel package does not import Workspace modules. Workspace owns this
    composition root so its business/provider surface can evolve without turning the
    execution substrate into a second Workspace package.
    """
    runtime = build_kernel_runtime()
    for spec in workspace_capabilities():
        runtime.registry.register(spec)
    register_workspace_providers(runtime.providers)
    return runtime
