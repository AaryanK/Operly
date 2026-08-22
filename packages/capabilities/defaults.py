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


def default_registry(enabled_plugins=None) -> CapabilityRegistry:
    """Build the current built-in registry during manifest-runtime migration.

    This remains bootstrap code, not the long-term extension mechanism. Discovery
    capabilities are registered against the finished registry so agents can search
    capability metadata without receiving every schema up front.
    """

    def enabled(tenant_id, definition):
        return (
            definition.integration_provider is None
            or definition.integration_provider == "discord"
            or enabled_plugins is None
            or definition.id in enabled_plugins
        )

    registry = CapabilityRegistry(enabled_resolver=enabled)
    for provider in (
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
    ):
        registry.register(provider)
    registry.register(CapabilityDiscoveryProvider(registry))
    return registry
