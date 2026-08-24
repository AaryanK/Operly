from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.plugins.runtime import default_plugin_runtime


async def emit_workspace_event(
    db: AsyncSession,
    *,
    plugin_id: str,
    event_id: str,
    tenant_id: str,
    payload: dict[str, Any] | None = None,
    actor_type: str = "system",
    actor_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
):
    """Emit one plugin-declared event into Operly's existing workspace event store.

    This is the producer contract future plugins should use. A plugin can only emit
    events owned by its own manifest, payloads are checked against the declared
    schema, and the durable BusinessEvent machinery decides which Tasks wake up.
    Plugins therefore never import Task/scheduler/agent internals.
    """
    plugin = str(plugin_id or "").strip()
    event_name = str(event_id or "").strip()
    workspace = str(tenant_id or "").strip()
    if not plugin or not event_name or not workspace:
        raise ValueError("plugin_id_event_id_tenant_id_required")

    manifests = default_plugin_runtime().manifests
    owner = manifests.owner_for_event(event_name)
    if owner is None:
        raise LookupError(f"Plugin event is not registered: {event_name}")
    if owner != plugin:
        raise PermissionError(
            f"Plugin {plugin} cannot emit event {event_name} owned by {owner}"
        )
    event = manifests.event(event_name)
    if event.scope not in {"workspace", "either"}:
        raise PermissionError(f"Event {event_name} is not a workspace event")

    normalized_payload = dict(payload or {})
    if event.payload_schema:
        from packages.capabilities.validation import validate_arguments

        validate_arguments(event.payload_schema, normalized_payload)

    from packages.company.events.service import append_event

    event_metadata = dict(metadata or {})
    event_metadata["plugin_id"] = plugin
    return await append_event(
        db,
        tenant_id=workspace,
        event_type=event_name,
        payload=normalized_payload,
        actor_type=actor_type,
        actor_id=actor_id,
        source=f"plugin:{plugin}",
        correlation_id=correlation_id,
        causation_id=causation_id,
        metadata=event_metadata,
    )
