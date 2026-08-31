"""Workspace-owned external integrations.

Each provider package resolves Operly workspace authority, the workspace-owned
connector/account binding, and provider-side scopes/resource permissions before
execution. AI is intentionally absent from this layer.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packages.kernel.contracts import CapabilitySpec
    from packages.kernel.providers import ProviderRegistry


def integration_capabilities() -> tuple["CapabilitySpec", ...]:
    from packages.workspace_modules.integrations.canva import workspace_canva_capabilities
    from packages.workspace_modules.integrations.canva.authoring import workspace_canva_authoring_capabilities
    from packages.workspace_modules.integrations.discord import workspace_discord_capabilities
    from packages.workspace_modules.integrations.google import workspace_google_capabilities
    return (
        *workspace_google_capabilities(),
        *workspace_canva_capabilities(),
        *workspace_canva_authoring_capabilities(),
        *workspace_discord_capabilities(),
    )


def register_integration_providers(providers: "ProviderRegistry") -> None:
    from packages.workspace_modules.integrations.canva import AvailableWorkspaceCanvaProvider, PROVIDER_ID as CANVA_PROVIDER_ID
    from packages.workspace_modules.integrations.canva.authoring import AvailableWorkspaceCanvaAuthoringProvider, PROVIDER_ID as CANVA_AUTHORING_PROVIDER_ID
    from packages.workspace_modules.integrations.discord import AvailableWorkspaceDiscordProvider, PROVIDER_ID as DISCORD_PROVIDER_ID
    from packages.workspace_modules.integrations.google import AvailableWorkspaceGoogleProvider, PROVIDER_ID as GOOGLE_PROVIDER_ID
    providers.register(GOOGLE_PROVIDER_ID, AvailableWorkspaceGoogleProvider())
    providers.register(CANVA_PROVIDER_ID, AvailableWorkspaceCanvaProvider())
    providers.register(CANVA_AUTHORING_PROVIDER_ID, AvailableWorkspaceCanvaAuthoringProvider())
    providers.register(DISCORD_PROVIDER_ID, AvailableWorkspaceDiscordProvider())


__all__ = ["integration_capabilities", "register_integration_providers"]
