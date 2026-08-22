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
from packages.connectors.discord.provider import DiscordProvider
from packages.connectors.google_provider import GmailProvider, GoogleCalendarProvider
from packages.plugins.runtime import default_plugin_runtime


def default_registry(enabled_plugins=None) -> CapabilityRegistry:
    """Build the canonical capability registry.

    Built-ins are still explicit bootstrap entries during migration. Installed
    plugin contributions are appended from PluginRuntime, so new external plugins
    no longer require edits to the agent loop or this registry factory.
    """

    def enabled(tenant_id, definition):
        return (
            definition.integration_provider is None
            or definition.integration_provider == "discord"
            or enabled_plugins is None
            or definition.id in enabled_plugins
        )

    registry = CapabilityRegistry(enabled_resolver=enabled)
    builtins = (
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
    for provider in builtins:
        registry.register(provider)

    for provider in default_plugin_runtime().capability_providers():
        registry.register(provider)

    # Discovery is registered last and points at the completed registry so plugin
    # contributions are immediately searchable without changing model prompts.
    registry.register(CapabilityDiscoveryProvider(registry))
    return registry
