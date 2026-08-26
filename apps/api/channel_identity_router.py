from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import (
    AccountAuthContext,
    AuthContext,
    get_account_auth_context,
    get_auth_context,
    get_db,
)
from packages.channels.identity import IdentityService
from packages.channels.linking import IdentityLinkService
from packages.database.channel_models import ExternalIdentity
from packages.security.human_identity import HumanIdentityService
from packages.security.temporal_context import set_user_timezone, user_timezone, validate_timezone

router = APIRouter(prefix="/api/identities", tags=["identities"])


class ClaimIdentityInput(BaseModel):
    token: str = Field(min_length=20, max_length=500)


class TimezoneInput(BaseModel):
    timezone: str = Field(min_length=1, max_length=100)


def _provider(value: str) -> str:
    provider = "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum() or ch in {"-", "_"})
    if not provider or len(provider) > 40:
        raise HTTPException(422, "Invalid identity provider")
    return provider


@router.get("")
async def identities(
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(ExternalIdentity)
            .where(ExternalIdentity.user_id == auth.user.id)
            .order_by(ExternalIdentity.created_at)
        )
    ).all()
    return [
        {
            "id": row.id,
            "provider": row.provider,
            "display_name": row.display_name,
            "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        }
        for row in rows
    ]


@router.get("/graph")
async def identity_graph(
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Resolve all known authentication, channel and connector identities to one human."""
    try:
        snapshot = await HumanIdentityService.snapshot(db, user_id=auth.user.id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    return snapshot.as_dict()


@router.get("/preferences/timezone")
async def get_timezone_preference(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    value = await user_timezone(db, auth.user.id)
    return {
        "timezone": value,
        "workspace_fallback": validate_timezone(auth.tenant.timezone),
    }


@router.put("/preferences/timezone")
async def put_timezone_preference(
    payload: TimezoneInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        value = await set_user_timezone(db, user_id=auth.user.id, timezone_name=payload.timezone)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    await db.commit()
    return {"ok": True, "timezone": value}


@router.post("/{provider}/link-code")
async def provider_link_code(
    provider: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    name = _provider(provider)
    challenge = await IdentityLinkService.create_from_operly(
        db,
        user_id=auth.user.id,
        provider=name,
    )
    await db.commit()
    return {
        "provider": name,
        "code": challenge.code,
        "expires_at": challenge.expires_at.isoformat(),
        "instruction": f"Complete the {name} connector's Operly link flow with this one-time code.",
    }


@router.get("/{provider}/claim-info")
async def provider_claim_info(
    provider: str,
    token: str = Query(..., min_length=20, max_length=500),
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    name = _provider(provider)
    info = await IdentityLinkService.inspect_channel_token(db, provider=name, token=token)
    if not info:
        raise HTTPException(404, "Identity link is invalid or expired")
    return {
        "provider": name,
        "display_name": info["display_name"],
        "expires_at": info["expires_at"].isoformat(),
        "operly_user": auth.user.display_name,
        "confirmation_required": True,
    }


@router.post("/{provider}/claim")
async def provider_claim(
    provider: str,
    payload: ClaimIdentityInput,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    name = _provider(provider)
    try:
        identity = await IdentityLinkService.claim_from_web(
            db,
            user_id=auth.user.id,
            provider=name,
            token=payload.token,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    await db.commit()
    return {
        "ok": True,
        "identity_id": identity.id,
        "provider": identity.provider,
        "display_name": identity.display_name,
    }


@router.delete("/{identity_id}")
async def unlink_identity(
    identity_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    removed = await IdentityService.unlink_external_identity(
        db,
        user_id=auth.user.id,
        identity_id=identity_id,
    )
    if not removed:
        raise HTTPException(404, "External identity not found")
    await db.commit()
    return {"ok": True}
