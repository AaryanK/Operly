from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.channels.linking import IdentityLinkService
from packages.database.channel_models import ExternalIdentity


router = APIRouter(prefix="/api/identities", tags=["identities"])


class ClaimIdentityInput(BaseModel):
    token: str = Field(min_length=20, max_length=500)


@router.get("")
async def identities(
    auth: AuthContext = Depends(get_auth_context),
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


@router.post("/discord/link-code")
async def discord_link_code(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    challenge = await IdentityLinkService.create_from_operly(
        db,
        user_id=auth.user.id,
        provider="discord",
    )
    await db.commit()
    return {
        "code": challenge.code,
        "expires_at": challenge.expires_at.isoformat(),
        "instruction": "In Discord, send `!operly link CODE` to the Operly bot.",
    }


@router.get("/discord/claim-info")
async def discord_claim_info(
    token: str = Query(..., min_length=20, max_length=500),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    info = await IdentityLinkService.inspect_channel_token(
        db,
        provider="discord",
        token=token,
    )
    if not info:
        raise HTTPException(404, "Identity link is invalid or expired")
    return {
        "provider": "discord",
        "display_name": info["display_name"],
        "expires_at": info["expires_at"].isoformat(),
        "operly_user": auth.user.display_name,
        "confirmation_required": True,
    }


@router.post("/discord/claim")
async def discord_claim(
    payload: ClaimIdentityInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        identity = await IdentityLinkService.claim_from_web(
            db,
            user_id=auth.user.id,
            provider="discord",
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
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.id == identity_id,
            ExternalIdentity.user_id == auth.user.id,
        )
    )
    if row is None:
        raise HTTPException(404, "External identity not found")
    await db.delete(row)
    await db.commit()
    return {"ok": True}
