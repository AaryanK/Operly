from dataclasses import dataclass

from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.security import decode_token
from packages.database.db import SessionFactory
from packages.database.models import AppUser, Tenant, TenantMember


async def get_db():
    async with SessionFactory() as session:
        yield session


@dataclass(slots=True)
class AuthContext:
    user: AppUser
    tenant: Tenant
    role: str


async def get_auth_context(
    operly_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    token = operly_session

    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        payload = decode_token(token)
    except ValueError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error

    user_id = payload.get("user_id")
    tenant_id = payload.get("tenant_id")

    membership = await db.scalar(
        select(TenantMember).where(
            TenantMember.user_id == user_id,
            TenantMember.tenant_id == tenant_id,
        )
    )
    user = await db.get(AppUser, user_id)
    tenant = await db.get(Tenant, tenant_id)

    if not membership or not user or not user.active or not tenant:
        raise HTTPException(status_code=401, detail="Access denied")

    return AuthContext(
        user=user,
        tenant=tenant,
        role=membership.role,
    )
