from packages.capabilities.action_provider import ActionLifecycleProvider
from packages.capabilities.business_provider import UnifiedBusinessProvider
from packages.capabilities.context_provider import ContextProvider
from packages.capabilities.discovery_provider import CapabilityDiscoveryProvider
from packages.capabilities.gmail_draft_provider import GmailDraftLifecycleProvider
from packages.capabilities.history_provider import ConversationHistoryProvider
from packages.capabilities.message_curation import MessageCurationProvider
from packages.capabilities.model_provider import ModelInvocationProvider
from packages.capabilities.operations_provider import OperationsProvider
from packages.capabilities.personal_provider import PersonalRuntimeProvider
from packages.capabilities.providers import (
    CompanyProvider,
    MessagingProvider,
    OperlyAnalyticsProvider,
    PresenceOperationsProvider,
    ResearchProvider,
)
from packages.capabilities.registry import CapabilityRegistry
from packages.capabilities.reminder_provider import ReminderProvider
from packages.capabilities.solution_provider import UnifiedSolutionProvider
from packages.capabilities.studio_provider import StudioProvider
from packages.capabilities.website_provider import UnifiedWebsiteProvider
from packages.capabilities.workspace_provider import WorkspaceProvider
from packages.connectors.discord.lifecycle import discord_plugin_lifecycle
from packages.connectors.discord.provider import DiscordProvider
from packages.connectors.google_provider import GmailProvider, GoogleCalendarProvider
from packages.plugins import (
    PermissionSpec,
    PluginContribution,
    PluginLifecycleSpec,
    PluginManifest,
    default_plugin_runtime,
)


def _builtin_providers():
    return (
        CompanyProvider(),
        ResearchProvider(),
        OperlyAnalyticsProvider(),
        PersonalRuntimeProvider(),
        ContextProvider(),
        ConversationHistoryProvider(),
        ActionLifecycleProvider(),
        UnifiedWebsiteProvider(),
        UnifiedBusinessProvider(),
        WorkspaceProvider(),
        OperationsProvider(),
        StudioProvider(),
        ReminderProvider(),
        MessagingProvider(),
        MessageCurationProvider(),
        UnifiedSolutionProvider(),
        PresenceOperationsProvider(),
        ModelInvocationProvider(),
        DiscordProvider(),
        GmailProvider(),
        GmailDraftLifecycleProvider(),
        GoogleCalendarProvider(),
    )


def bootstrap_builtin_plugins() -> None:
    """Register current first-party providers through the universal plugin runtime."""
    runtime = default_plugin_runtime()
    for provider in _builtin_providers():
        permissions = sorted(
            {
                permission
                for definition in provider.capabilities
                for permission in definition.permissions
            }
        )
        integrations = sorted(
            {
                definition.integration_provider
                for definition in provider.capabilities
                if definition.integration_provider
            }
        )
        is_discord = isinstance(provider, DiscordProvider)
        manifest = PluginManifest(
            id=f"builtin:{provider.name}",
            version="1.0.0",
            display_name=provider.name.replace("_", " ").title(),
            description=f"Built-in Operly capability provider: {provider.name}",
            capabilities=tuple(provider.capabilities),
            permissions=tuple(PermissionSpec(permission) for permission in permissions),
            connectors=tuple(integrations),
            lifecycle=(
                PluginLifecycleSpec(start_on_boot=True, supports_health=True)
                if is_discord
                else None
            ),
            metadata={"builtin": True, "provider_name": provider.name},
        )
        runtime.register(
            PluginContribution(
                manifest=manifest,
                capability_provider=provider,
                lifecycle=discord_plugin_lifecycle if is_discord else None,
            ),
            replace=True,
        )


def default_registry(enabled_plugins=None) -> CapabilityRegistry:
    """Build the canonical capability registry from PluginRuntime contributions.

    First-party capabilities and externally installed capabilities now take the
    same registration path. The discovery kernel remains session-bound because it
    must point at this tenant-specific completed registry.
    """

    def enabled(tenant_id, definition):
        return (
            definition.integration_provider is None
            or definition.integration_provider == "discord"
            or enabled_plugins is None
            or definition.id in enabled_plugins
        )

    bootstrap_builtin_plugins()
    registry = CapabilityRegistry(enabled_resolver=enabled)
    for provider in default_plugin_runtime().capability_providers():
        registry.register(provider)

    registry.register(CapabilityDiscoveryProvider(registry))
    return registry
