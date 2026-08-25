from packages.capabilities.action_provider import ActionLifecycleProvider
from packages.capabilities.app_identity_provider import AppIdentityProvider
from packages.capabilities.artifact_provider import ArtifactProvider
from packages.capabilities.calendar_semantics_provider import CalendarSemanticsProvider
from packages.capabilities.computer_provider import AgentComputerProvider
from packages.capabilities.context_provider import ContextProvider
from packages.capabilities.crm_read_provider import CRMReadProvider
from packages.capabilities.discovery_provider import CapabilityDiscoveryProvider
from packages.capabilities.event_provider import EventDiscoveryProvider
from packages.capabilities.eventful_business_provider import EventfulUnifiedBusinessProvider
from packages.capabilities.gmail_artifact_provider import GmailArtifactProvider
from packages.capabilities.gmail_draft_provider import GmailDraftLifecycleProvider
from packages.capabilities.gmail_read_provider import GmailReadProvider
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
from packages.capabilities.relational_data_provider import RelationalDataProvider
from packages.capabilities.reminder_provider import ReminderProvider
from packages.capabilities.software_build_provider import SoftwareBuildProvider
from packages.capabilities.software_project_provider import SoftwareProjectProvider
from packages.capabilities.solution_provider import UnifiedSolutionProvider
from packages.capabilities.studio_provider import StudioProvider
from packages.capabilities.universal_task_provider import UniversalTaskProvider
from packages.capabilities.web_read_provider import PublicWebReadProvider
from packages.capabilities.website_provider import UnifiedWebsiteProvider
from packages.capabilities.workspace_entity_provider import WorkspaceEntityProvider
from packages.capabilities.workspace_provider import WorkspaceProvider
from packages.connectors.discord.lifecycle import discord_plugin_lifecycle
from packages.connectors.discord.provider import DiscordProvider
from packages.connectors.google_provider import GmailProvider, GoogleCalendarProvider
from packages.plugins import (
    EventSpec,
    PermissionSpec,
    PluginContribution,
    PluginLifecycleSpec,
    PluginManifest,
    default_plugin_runtime,
)


def _builtin_providers():
    # FileRuntimeProvider intentionally loads only when the registry is being
    # bootstrapped. Its attachment stack uses channel formatting, whose package
    # surface can load the agent harness and therefore this module. Keeping this
    # heavyweight provider out of module-import time prevents a registry ↔ agent ↔
    # attachment circular import while preserving one canonical provider instance
    # for AI, Studio, MCP and workflows.
    from packages.capabilities.file_authoring_provider import FileAuthoringProvider
    from packages.capabilities.file_runtime_provider import FileRuntimeProvider

    return (
        CompanyProvider(),
        ResearchProvider(),
        OperlyAnalyticsProvider(),
        PersonalRuntimeProvider(),
        ContextProvider(),
        ConversationHistoryProvider(),
        ActionLifecycleProvider(),
        UnifiedWebsiteProvider(),
        EventfulUnifiedBusinessProvider(),
        CRMReadProvider(),
        WorkspaceProvider(),
        OperationsProvider(),
        StudioProvider(),
        SoftwareProjectProvider(),
        SoftwareBuildProvider(),
        RelationalDataProvider(),
        WorkspaceEntityProvider(),
        AppIdentityProvider(),
        ReminderProvider(),
        UniversalTaskProvider(),
        PublicWebReadProvider(),
        ArtifactProvider(),
        FileRuntimeProvider(),
        FileAuthoringProvider(),
        AgentComputerProvider(),
        MessagingProvider(),
        MessageCurationProvider(),
        UnifiedSolutionProvider(),
        PresenceOperationsProvider(),
        ModelInvocationProvider(),
        DiscordProvider(),
        GmailProvider(),
        GmailReadProvider(),
        GmailDraftLifecycleProvider(),
        GmailArtifactProvider(),
        GoogleCalendarProvider(),
        CalendarSemanticsProvider(),
    )


def _provider_events(provider) -> tuple[EventSpec, ...]:
    """Normalize events from providers/capabilities into the plugin manifest."""
    output: dict[str, EventSpec] = {}
    for raw in getattr(provider, "events", ()) or ():
        if isinstance(raw, str):
            event = EventSpec(raw)
        else:
            event = raw
        if str(getattr(event, "id", "")).strip():
            output[event.id] = event
    for definition in provider.capabilities:
        for event_id in definition.event_capabilities:
            value = str(event_id or "").strip()
            if value and value not in output:
                output[value] = EventSpec(
                    value,
                    description=f"Event emitted by {definition.id}",
                )
    return tuple(output[event_id] for event_id in sorted(output))


def _capability_runtime_events() -> tuple[EventSpec, ...]:
    """Public trigger surface over the ActionService capability lifecycle.

    ActionService already durably records these events for every workspace capability
    invocation. Registering them here makes that existing real-time stream available
    to the generic workflow compiler without adding per-capability trigger code.
    """
    payload_schema = {
        "type": "object",
        "properties": {
            "action_id": {"type": "string"},
            "capability": {"type": "string"},
            "status": {"type": "string"},
            "principal_id": {"type": "string"},
            "client_id": {"type": "string"},
            "origin": {"type": "string"},
            "connector_id": {"type": "string"},
            "resource_type": {"type": "string"},
            "scope_kind": {"type": "string"},
        },
        "required": ["action_id", "capability", "status"],
        "additionalProperties": True,
    }
    descriptions = {
        "action.proposed": "A governed Operly capability action was proposed.",
        "action.waiting_approval": "A capability action is waiting for human approval.",
        "action.approved": "A previously gated capability action was approved.",
        "action.executing": "A capability action started executing through the firewall.",
        "action.executed": "A capability provider returned successfully and is awaiting verification.",
        "action.verifying": "Operly started verifying a capability result.",
        "action.verified": "A capability action completed and its result was verified.",
        "action.rejected": "A capability action was rejected by policy or a human approver.",
        "action.failed": "A capability action failed before successful verification.",
        "action.verification_failed": "A capability action ran but failed result verification.",
    }
    return tuple(
        EventSpec(
            event_id,
            description=description,
            payload_schema=payload_schema,
            scope="workspace",
            tags=frozenset({"capability", "workflow", "runtime", "realtime"}),
        )
        for event_id, description in descriptions.items()
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
            events=_provider_events(provider),
            lifecycle=(
                PluginLifecycleSpec(start_on_boot=True, supports_health=True)
                if is_discord
                else None
            ),
            metadata={"builtin": True, "provider_name": provider.name},
        )
        delivery_adapters = ()
        if is_discord:
            from packages.connectors.discord.task_delivery import DiscordTaskDeliveryAdapter

            delivery_adapters = (DiscordTaskDeliveryAdapter(),)
        runtime.register(
            PluginContribution(
                manifest=manifest,
                capability_provider=provider,
                lifecycle=discord_plugin_lifecycle if is_discord else None,
                task_delivery_adapters=delivery_adapters,
            ),
            replace=True,
        )

    # ActionService is the common governed seam for agents, workflows, Studio and MCP.
    # Its durable lifecycle stream therefore becomes a generic event source that any
    # workflow may subscribe to, filtered by payload.capability when desired.
    runtime.register(
        PluginContribution(
            manifest=PluginManifest(
                id="builtin:capability_runtime",
                version="1.0.0",
                display_name="Capability Runtime",
                description="Real-time governed capability lifecycle events shared by every Operly surface.",
                events=_capability_runtime_events(),
                metadata={"builtin": True, "platform_runtime": True, "shared_with_studio": True},
            ),
        ),
        replace=True,
    )

    # Task execution is a platform lifecycle, not a Discord lifecycle. It polls the
    # existing ScheduledJob wake-up rows and routes outputs through plugin adapters.
    from packages.tasks.delivery import OperlyConversationDeliveryAdapter
    from packages.tasks.runtime import task_plugin_lifecycle

    runtime.register(
        PluginContribution(
            manifest=PluginManifest(
                id="builtin:task_runtime",
                version="1.0.0",
                display_name="Task Runtime",
                description="Channel-agnostic durable Task dispatcher and Operly delivery adapter.",
                lifecycle=PluginLifecycleSpec(start_on_boot=True, supports_health=True),
                metadata={"builtin": True, "platform_runtime": True},
            ),
            lifecycle=task_plugin_lifecycle,
            task_delivery_adapters=(OperlyConversationDeliveryAdapter(),),
        ),
        replace=True,
    )


def default_registry(enabled_plugins=None, *, config_resolver=None) -> CapabilityRegistry:
    """Build the canonical capability registry from PluginRuntime contributions.

    ``enabled_plugins`` remains a compatibility filter. New runtime code should
    prefer ``config_resolver`` so an unavailable connector capability stays
    discoverable and can explain *why* it is unavailable instead of disappearing.
    """

    def enabled(tenant_id, definition):
        return (
            definition.integration_provider is None
            or definition.integration_provider == "discord"
            or enabled_plugins is None
            or definition.id in enabled_plugins
        )

    bootstrap_builtin_plugins()
    registry = CapabilityRegistry(
        enabled_resolver=enabled,
        config_resolver=config_resolver,
    )
    for provider in default_plugin_runtime().capability_providers():
        registry.register(provider)

    registry.register(CapabilityDiscoveryProvider(registry))
    registry.register(EventDiscoveryProvider())
    return registry
