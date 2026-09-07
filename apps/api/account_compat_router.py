from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from apps.api.session import create_workspace as create_auth_workspace
from apps.api.session import workspaces as auth_workspaces
from packages.database.models import AppUser


router = APIRouter(tags=["account-shell-compat"])


class AccountProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=200)


def _clean_name(value: str) -> str:
    return " ".join(str(value or "").replace("\x00", "").split()).strip()[:200]


@router.get("/api/personal-agent/me")
async def account_me(auth: AccountAuthContext = Depends(get_account_auth_context)):
    return {
        "id": auth.user.id,
        "email": auth.user.email,
        "display_name": auth.user.display_name,
        "current_workspace_id": auth.session.tenant_id,
    }


@router.patch("/api/personal-agent/me")
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
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "current_workspace_id": auth.session.tenant_id,
    }


@router.get("/api/personal-agent/workspaces")
async def account_workspaces(
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await auth_workspaces(auth, db)


@router.post("/api/workspaces", status_code=201)
async def create_workspace_compat(
    payload: dict,
    request: Request,
    response: Response,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    # Delegate to the canonical auth endpoint so workspace creation keeps the
    # existing session rotation, CSRF rotation, audit event, and owner membership.
    result = await create_auth_workspace(payload, request, response, auth, db)
    workspace = dict(result["workspace"])
    workspace["current"] = True
    return workspace
