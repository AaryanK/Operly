"""Workspace-owned deterministic tools.

Workspace business modules own their tools; external providers live under
``packages.workspace_modules.integrations``. Studio deployment, Agent Computer, and
the top-level Workflow package compose here over the generic Kernel execution/policy
substrate without creating parallel execution authority.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packages.kernel.contracts import CapabilitySpec
    from packages.kernel.providers import ProviderRegistry


def workspace_capabilities() -> tuple["CapabilitySpec", ...]:
    from packages.workflow import workflow_capabilities
    from packages.workspace_modules.agent_computer.native_tools import computer_native_capabilities
    from packages.workspace_modules.integrations import integration_capabilities
    from packages.workspace_modules.studio import workspace_studio_capabilities
    from packages.workspace_modules.tools.business import workspace_business_capabilities
    from packages.workspace_modules.tools.controls import workspace_control_capabilities
    from packages.workspace_modules.tools.records import workspace_record_capabilities
    from packages.workspace_modules.tools.system import workspace_system_capabilities

    return (
        *workspace_system_capabilities(),
        *workspace_control_capabilities(),
        *workspace_business_capabilities(),
        *workspace_record_capabilities(),
        *workspace_studio_capabilities(),
        *computer_native_capabilities(),
        *integration_capabilities(),
        *workflow_capabilities(),
    )


def register_workspace_providers(providers: "ProviderRegistry") -> None:
    from packages.workflow import PROVIDER_ID as WORKFLOW_PROVIDER_ID, WorkflowProvider
    from packages.workspace_modules.agent_computer.native_tools import (
        PROVIDER_ID as AGENT_COMPUTER_PROVIDER_ID,
        AgentComputerProvider,
    )
    from packages.workspace_modules.integrations import register_integration_providers
    from packages.workspace_modules.studio import PROVIDER_ID as STUDIO_PROVIDER_ID, WorkspaceStudioProvider
    from packages.workspace_modules.tools.availability import AvailableWorkspaceBusinessProvider, AvailableWorkspaceControlProvider, AvailableWorkspaceOSProvider
    from packages.workspace_modules.tools.business import PROVIDER_ID as WORKSPACE_BUSINESS_PROVIDER_ID
    from packages.workspace_modules.tools.controls import PROVIDER_ID as WORKSPACE_CONTROL_PROVIDER_ID
    from packages.workspace_modules.tools.records import PROVIDER_ID as WORKSPACE_OS_PROVIDER_ID
    from packages.workspace_modules.tools.system import PROVIDER_ID as WORKSPACE_SYSTEM_PROVIDER_ID, WorkspaceSystemProvider

    providers.register(WORKSPACE_SYSTEM_PROVIDER_ID, WorkspaceSystemProvider())
    providers.register(WORKSPACE_CONTROL_PROVIDER_ID, AvailableWorkspaceControlProvider())
    providers.register(WORKSPACE_BUSINESS_PROVIDER_ID, AvailableWorkspaceBusinessProvider())
    providers.register(WORKSPACE_OS_PROVIDER_ID, AvailableWorkspaceOSProvider())
    providers.register(STUDIO_PROVIDER_ID, WorkspaceStudioProvider())
    providers.register(AGENT_COMPUTER_PROVIDER_ID, AgentComputerProvider())
    providers.register(WORKFLOW_PROVIDER_ID, WorkflowProvider())
    register_integration_providers(providers)


__all__ = ["register_workspace_providers", "workspace_capabilities"]
