import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app_identity_router import admin_router as app_identity_admin_router
from apps.api.app_identity_router import runtime_router as app_identity_runtime_router
from apps.api.dependencies import AuthContext, get_auth_context, get_db
from apps.api.schemas import TenantUpdate
from apps.api.workspace_entities_router import router as workspace_entities_router
from packages.database.models import Tenant
from packages.model_runtime import (
    configured_portfolio,
    installed_model_providers,
    model_resources,
)
from packages.model_runtime.routing_policy import (
    configured_provider_count,
    role_routing_profiles,
)


router = APIRouter(prefix="/api", tags=["system"])
router.include_router(workspace_entities_router)
router.include_router(app_identity_runtime_router)
router.include_router(app_identity_admin_router)


def deployed_commit_sha() -> str:
    return (
        os.getenv("RAILWAY_GIT_COMMIT_SHA")
        or os.getenv("GIT_COMMIT_SHA")
        or os.getenv("SOURCE_VERSION")
        or "unknown"
    ).strip()[:64]


@router.get("/health")
async def health():
    return {"ok": True, "service": "operly", "commit_sha": deployed_commit_sha()}


@router.get("/me")
async def me(auth: AuthContext = Depends(get_auth_context)):
    return {
        "user": {
            "id": auth.user.id,
            "email": auth.user.email,
            "display_name": auth.user.display_name,
        },
        "tenant": {
            "id": auth.tenant.id,
            "name": auth.tenant.name,
            "timezone": auth.tenant.timezone,
        },
        "role": auth.role,
    }


@router.get("/models")
async def model_cards(auth: AuthContext = Depends(get_auth_context)):
    del auth
    cards = [resource.as_dict() for resource in model_resources()]
    provider_names = installed_model_providers()
    configured_names = sorted(
        {
            card["provider"]
            for card in cards
            if card.get("provider_configured")
        }
    )
    return {
        "providers": [
            {
                "id": provider,
                "installed": True,
                "configured": provider in configured_names,
                "model_count": sum(
                    1 for card in cards if card["provider"] == provider
                ),
            }
            for provider in provider_names
        ],
        "configured_provider_count": configured_provider_count(),
        "roles": role_routing_profiles(),
        "legacy_role_overrides": configured_portfolio(),
        "models": cards,
    }


@router.patch("/settings/tenant")
async def update_tenant(
    payload: TenantUpdate,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    tenant = await db.get(Tenant, auth.tenant.id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    tenant.name = payload.name.strip()
    tenant.timezone = payload.timezone.strip() or "UTC"
    await db.commit()
    return {"id": tenant.id, "name": tenant.name, "timezone": tenant.timezone}
