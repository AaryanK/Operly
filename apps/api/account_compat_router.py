from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from apps.api.session import create_workspace as create_auth_workspace
from apps.api.session import workspaces as auth_workspaces
from packages.database.models import AppUser, AuthIdentity


router = APIRouter(tags=["account-shell-compat"])


class AccountProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=200)


def _clean_name(value: str) -> str:
    return " ".join(str(value or "").replace("\x00", "").split()).strip()[:200]


async def _profile_payload(db: AsyncSession, user: AppUser, current_workspace_id: str | None) -> dict:
    identities = list(
        (
            await db.scalars(
                select(AuthIdentity)
                .where(AuthIdentity.user_id == user.id)
                .order_by(AuthIdentity.provider, AuthIdentity.created_at)
            )
        ).all()
    )
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "current_workspace_id": current_workspace_id,
        # Sign-in identities are authentication metadata only. They intentionally
        # do not imply Gmail/Calendar/Drive authorization or connector credentials.
        "auth_identities": [
            {
                "provider": identity.provider,
                "account": identity.provider_email or (user.email if identity.provider == "password" else None),
            }
            for identity in identities
        ],
    }


@router.get("/api/auth/me")
@router.get("/api/personal-agent/me", include_in_schema=False)
async def account_me(
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await _profile_payload(db, auth.user, auth.session.tenant_id)


@router.patch("/api/auth/me")
@router.patch("/api/personal-agent/me", include_in_schema=False)
async def update_account_me(
    payload: AccountProfilePatch,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(AppUser, auth.user.id)
    if not user or not user.active:
        raise HTTPException(status_code=401, detail="Account unavailable")
    name = _clean_name(payload.display_name)
    if not name:
        raise HTTPException(status_code=422, detail="Display name is required")
    user.display_name = name
    user.updated_at = datetime.utcnow()
    await db.commit()
    return await _profile_payload(db, user, auth.session.tenant_id)


@router.get("/api/personal-agent/workspaces", include_in_schema=False)
async def account_workspaces(
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await auth_workspaces(auth, db)


@router.post("/api/workspaces", status_code=201, include_in_schema=False)
async def create_workspace_compat(
    payload: dict,
    request: Request,
    response: Response,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    # Legacy alias only. The current frontend uses /api/auth/workspaces directly.
    # Delegate so any old client still gets canonical session/CSRF rotation.
    result = await create_auth_workspace(payload, request, response, auth, db)
    workspace = dict(result["workspace"])
    workspace["current"] = True
    return workspace
