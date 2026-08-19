from packages.capabilities.business_provider import UnifiedBusinessProvider
from packages.capabilities.message_curation import MessageCurationProvider
from packages.capabilities.operations_provider import OperationsProvider
from packages.capabilities.providers import (
    CompanyProvider,
    MessagingProvider,
    OperlyAnalyticsProvider,
    OperlyWebsiteProvider,
    PresenceOperationsProvider,
    ResearchProvider,
    SolutionProvider,
)
from packages.capabilities.registry import CapabilityRegistry
from packages.capabilities.studio_provider import StudioProvider
from packages.capabilities.workspace_provider import WorkspaceProvider
from packages.connectors.google_provider import GmailProvider, GoogleCalendarProvider


def default_registry(enabled_plugins=None) -> CapabilityRegistry:
    """Build the single canonical runtime registry.

    Legacy provider definitions remain importable during migration, but the live
    execution path only registers the unified providers listed here.
    """

    def enabled(tenant_id, definition):
        return (
            definition.integration_provider is None
            or enabled_plugins is None
            or definition.id in enabled_plugins
        )

    registry = CapabilityRegistry(enabled_resolver=enabled)
    for provider in (
        CompanyProvider(),
        ResearchProvider(),
        OperlyAnalyticsProvider(),
        OperlyWebsiteProvider(),
        UnifiedBusinessProvider(),
        WorkspaceProvider(),
        OperationsProvider(),
        StudioProvider(),
        MessagingProvider(),
        MessageCurationProvider(),
        SolutionProvider(),
        PresenceOperationsProvider(),
        GmailProvider(),
        GoogleCalendarProvider(),
    ):
        registry.register(provider)
    return registry
