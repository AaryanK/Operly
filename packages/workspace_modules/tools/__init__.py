"""Workspace-owned deterministic tools.

Workspace business modules own their tools; external providers live under
``packages.workspace_modules.integrations``. The generic Kernel supplies only the
cross-scope execution/policy substrate.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packages.kernel.contracts import CapabilitySpec
    from packages.kernel.providers import ProviderRegistry


def workspace_capabilities() -> tuple["CapabilitySpec", ...]:
    from packages.workspace_modules.integrations import integration_capabilities
    from packages.workspace_modules.tools.business import workspace_business_capabilities
    from packages.workspace_modules.tools.controls import workspace_control_capabilities
    from packages.workspace_modules.tools.records import workspace_record_capabilities
    from packages.workspace_modules.tools.system import workspace_system_capabilities

    return (
        *workspace_system_capabilities(),
        *workspace_control_capabilities(),
        *workspace_business_capabilities(),
        *workspace_record_capabilities(),
        *integration_capabilities(),
    )


def register_workspace_providers(providers: "ProviderRegistry") -> None:
    from packages.workspace_modules.integrations import register_integration_providers
    from packages.workspace_modules.tools.availability import AvailableWorkspaceBusinessProvider, AvailableWorkspaceControlProvider, AvailableWorkspaceOSProvider
    from packages.workspace_modules.tools.business import PROVIDER_ID as WORKSPACE_BUSINESS_PROVIDER_ID
    from packages.workspace_modules.tools.controls import PROVIDER_ID as WORKSPACE_CONTROL_PROVIDER_ID
    from packages.workspace_modules.tools.records import PROVIDER_ID as WORKSPACE_OS_PROVIDER_ID
    from packages.workspace_modules.tools.system import PROVIDER_ID as WORKSPACE_SYSTEM_PROVIDER_ID, WorkspaceSystemProvider

    providers.register(WORKSPACE_SYSTEM_PROVIDER_ID, WorkspaceSystemProvider())
    providers.register(WORKSPACE_CONTROL_PROVIDER_ID, AvailableWorkspaceControlProvider())
    providers.register(WORKSPACE_BUSINESS_PROVIDER_ID, AvailableWorkspaceBusinessProvider())
    providers.register(WORKSPACE_OS_PROVIDER_ID, AvailableWorkspaceOSProvider())
    register_integration_providers(providers)


__all__ = ["register_workspace_providers", "workspace_capabilities"]
