import os
import secrets

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from apps.api.schemas import LoginInput
from apps.api.security import create_token, verify_password
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.business_models import ActivityEvent

router = APIRouter(prefix="/api/session", tags=["session"])

SESSION_COOKIE = "operly_session"
CSRF_COOKIE = "operly_csrf"
SESSION_MAX_AGE = 60 * 60 * 24 * 7


def secure_cookie() -> bool:
    return os.getenv("PUBLIC_BASE_URL", "").lower().startswith("https://")


def set_session_cookies(response: Response, user_id: str, tenant_id: str, role: str) -> None:
    """Issue a fresh session and CSRF pair, preventing session fixation."""
    secure = secure_cookie()
    response.set_cookie(SESSION_COOKIE, create_token(user_id, tenant_id, role),
        max_age=SESSION_MAX_AGE, httponly=True, secure=secure, samesite="strict", path="/")
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(32),
        max_age=SESSION_MAX_AGE, httponly=False, secure=secure, samesite="strict", path="/")


class WorkspaceSwitchInput(BaseModel):
    tenant_id: str


@router.post("/login")
async def login(
    payload: LoginInput,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    email = payload.email.strip().lower()
    user = await db.scalar(select(AppUser).where(AppUser.email == email))

    if user is None or not user.active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    membership = await db.scalar(
        select(TenantMember)
        .where(TenantMember.user_id == user.id)
        .order_by(TenantMember.created_at)
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="No workspace assigned")

    set_session_cookies(response, user.id, membership.tenant_id, membership.role)

    return {"ok": True}


@router.get("/workspaces")
async def workspaces(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Tenant, TenantMember.role).join(TenantMember, TenantMember.tenant_id == Tenant.id)
        .where(TenantMember.user_id == auth.user.id).order_by(Tenant.name)
    )).all()
    return [{"id": tenant.id, "name": tenant.name, "role": role,
             "current": tenant.id == auth.tenant.id} for tenant, role in rows]


@router.post("/switch-workspace")
async def switch_workspace(payload: WorkspaceSwitchInput, response: Response,
    auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    membership = await db.scalar(select(TenantMember).where(
        TenantMember.user_id == auth.user.id, TenantMember.tenant_id == payload.tenant_id))
    tenant = await db.get(Tenant, payload.tenant_id) if membership else None
    if not membership or not tenant or not auth.user.active:
        raise HTTPException(status_code=404, detail="Workspace not found")
    set_session_cookies(response, auth.user.id, tenant.id, membership.role)
    db.add(ActivityEvent(tenant_id=tenant.id, event_type="workspace_switched",
        entity_type="tenant", entity_id=tenant.id, summary="Workspace selected",
        actor=auth.user.display_name))
    await db.commit()
    return {"ok": True, "workspace": {"id": tenant.id, "name": tenant.name}}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}
