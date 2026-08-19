import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from apps.api.schemas import TenantUpdate
from packages.database.models import Tenant

router = APIRouter(prefix="/api", tags=["system"])


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
