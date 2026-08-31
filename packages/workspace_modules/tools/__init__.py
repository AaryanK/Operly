"""Workspace-owned deterministic tools.

Every capability that reads or mutates Workspace OS state lives in this package.
The generic Kernel supplies contracts, policy, execution, validation, tracing, and
idempotency; Workspace modules own the actual business tools and providers.
"""

from packages.kernel.contracts import CapabilitySpec
from packages.kernel.providers import ProviderRegistry
from packages.workspace_modules.tools.availability import (
    AvailableWorkspaceBusinessProvider,
    AvailableWorkspaceControlProvider,
    AvailableWorkspaceGoogleProvider,
    AvailableWorkspaceOSProvider,
)
from packages.workspace_modules.tools.business import (
    PROVIDER_ID as WORKSPACE_BUSINESS_PROVIDER_ID,
    workspace_business_capabilities,
)
from packages.workspace_modules.tools.controls import (
    PROVIDER_ID as WORKSPACE_CONTROL_PROVIDER_ID,
    workspace_control_capabilities,
)
from packages.workspace_modules.tools.google import (
    PROVIDER_ID as WORKSPACE_GOOGLE_PROVIDER_ID,
    workspace_google_capabilities,
)
from packages.workspace_modules.tools.records import (
    PROVIDER_ID as WORKSPACE_OS_PROVIDER_ID,
    workspace_record_capabilities,
)
from packages.workspace_modules.tools.system import (
    PROVIDER_ID as WORKSPACE_SYSTEM_PROVIDER_ID,
    WorkspaceSystemProvider,
    workspace_system_capabilities,
)


def workspace_capabilities() -> tuple[CapabilitySpec, ...]:
    return (
        *workspace_system_capabilities(),
        *workspace_control_capabilities(),
        *workspace_business_capabilities(),
        *workspace_google_capabilities(),
        *workspace_record_capabilities(),
    )


def register_workspace_providers(providers: ProviderRegistry) -> None:
    providers.register(WORKSPACE_SYSTEM_PROVIDER_ID, WorkspaceSystemProvider())
    providers.register(WORKSPACE_CONTROL_PROVIDER_ID, AvailableWorkspaceControlProvider())
    providers.register(WORKSPACE_BUSINESS_PROVIDER_ID, AvailableWorkspaceBusinessProvider())
    providers.register(WORKSPACE_GOOGLE_PROVIDER_ID, AvailableWorkspaceGoogleProvider())
    providers.register(WORKSPACE_OS_PROVIDER_ID, AvailableWorkspaceOSProvider())


__all__ = [
    "register_workspace_providers",
    "workspace_capabilities",
]
