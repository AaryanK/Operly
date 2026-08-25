"""Reusable governed invocation seam for Personal AI and personal workflows.

Personal capabilities must not bypass ActionService merely because the caller is a
scheduled workflow rather than the interactive Personal AI surface. This helper keeps
Personal Google's account-owned dynamic registry while sending every supported
capability through the canonical CapabilityFirewall with a freshly resolved personal
ExecutionContext.
"""
from __future__ import annotations

from typing import Any

from packages.capabilities.firewall import ActionBackedCapabilityFirewall, CapabilityInvocation
from packages.capabilities.personal_google_provider import PersonalGoogleCapabilityProvider
from packages.database.db import session_scope
from packages.security.execution_context import (
    PERSONAL_EXECUTION_PERMISSIONS,
    resolve_personal_execution_context,
)
from packages.security.surfaces import SurfaceKind


async def invoke_personal_capability(
    service,
    *,
    user_id: str,
    capability_id: str,
    arguments: dict[str, Any],
    objective: str,
    call_id: str | None,
    channel: str = "personal_workflow",
    conversation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    focus_workspace_id: str | None = None,
) -> dict[str, Any]:
    resolved = service._definitions.get(capability_id)
    if resolved is None:
        return {
            "ok": False,
            "status": "DENIED",
            "error": "personal_capability_not_available",
        }
    provider, definition = resolved
    authority = set(PERSONAL_EXECUTION_PERMISSIONS)
    if not set(definition.permissions).issubset(authority):
        return {
            "ok": False,
            "status": "DENIED",
            "error": "personal_capability_authority_denied",
        }

    supplied = dict(metadata or {})
    surface = SurfaceKind.coerce(supplied.get("_surface_kind") or supplied.get("surface"))
    if surface not in {SurfaceKind.PERSONAL_PRIVATE, SurfaceKind.DISCORD_DM, SurfaceKind.SYSTEM_TASK}:
        surface = SurfaceKind.SYSTEM_TASK if "workflow" in str(channel) or "task" in str(channel) else SurfaceKind.PERSONAL_PRIVATE
    invocation_metadata = {
        **supplied,
        "personal_scope": True,
        "shared_surface": False,
        "is_direct": True,
        "_surface_kind": surface.value,
        "surface": surface.value,
        "call_id": call_id,
    }

    async with session_scope() as db:
        execution = await resolve_personal_execution_context(
            db,
            user_id=user_id,
            channel=channel,
            surface=surface,
            conversation_id=conversation_id,
            metadata=invocation_metadata,
            focus_workspace_id=focus_workspace_id,
        )
        registry = (
            await provider.registry_for(db, user_id=user_id)
            if isinstance(provider, PersonalGoogleCapabilityProvider)
            else service.registry
        )
        availability = registry.availability(
            execution.scope_id,
            capability_id,
            authority=authority,
        )
        if not availability.available:
            return {
                "ok": False,
                "status": "DENIED",
                "error": "personal_capability_unavailable",
                "availability": availability.as_dict(),
            }

        result = await ActionBackedCapabilityFirewall(registry).invoke(
            CapabilityInvocation(
                capability_id=capability_id,
                arguments=dict(arguments),
                objective=str(objective or "Personal capability invocation")[:12000],
                rationale=f"Personal runtime selected {capability_id} for the current objective",
                expected_outcome=definition.description,
                call_id=call_id,
                channel=channel,
                metadata=invocation_metadata,
            ),
            execution,
        )
        return result.as_dict()


__all__ = ["invoke_personal_capability"]
