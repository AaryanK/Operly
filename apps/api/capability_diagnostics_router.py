from fastapi import APIRouter, Depends, Query

from apps.api.dependencies import AuthContext, get_auth_context
from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext


router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])


@router.get("/{capability_id}/availability")
async def capability_availability(
    capability_id: str,
    surface: str = Query(default="web", max_length=40),
    conversation_id: str | None = Query(default=None, max_length=120),
    auth: AuthContext = Depends(get_auth_context),
):
    """Explain every application gate without exposing connector secrets."""
    harness = PluginAgentHarness()
    context = PluginInvocationContext(
        tenant_id=auth.tenant.id,
        user_id=auth.user.id,
        role=auth.role,
        objective=f"Explain availability of {capability_id}",
        channel=surface,
        metadata={
            "_conversation_id": conversation_id,
            "shared_surface": surface not in {"web", "dm", "personal"},
            "is_direct": surface in {"dm", "personal"},
            "diagnostic": True,
        },
    )
    availability = await harness.availability(capability_id, context)
    registry = await harness.registry_for(context)
    authority = await harness.authority_for(context)
    try:
        definition = registry.definition(capability_id)
        registered = True
        permissions = list(definition.permissions)
        integration = definition.integration_provider
    except LookupError:
        registered = False
        permissions = []
        integration = None

    return {
        "capabilityId": capability_id,
        "workspaceId": auth.tenant.id,
        "surface": surface,
        "registered": registered,
        "integrationProvider": integration,
        "requiredPermissions": permissions,
        "authoritySatisfied": bool(registered and set(permissions).issubset(authority)),
        "availability": availability,
        "gates": {
            "registration": "pass" if registered else "fail",
            "connector": "fail" if availability.get("missingConnector") else "pass",
            "oauthScopes": "fail" if availability.get("missingScopes") else "pass",
            "permission": "fail" if availability.get("permissionDenied") else "pass",
            "surfacePolicy": "fail" if availability.get("surfaceHidden") else "pass",
            "sessionExposure": "pass" if availability.get("exposed") else "not_exposed",
            "providerHealth": "fail" if availability.get("healthy") is False else "pass_or_unknown",
        },
    }
