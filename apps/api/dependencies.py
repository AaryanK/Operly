from dataclasses import dataclass

from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth_cookies import session_secret_from_request
from apps.api.security import hash_token
from packages.database.db import SessionFactory
from packages.database.models import AppUser, AuthSession, Tenant, TenantMember


async def get_db():
    async with SessionFactory() as session:
        yield session


@dataclass(slots=True)
class AuthContext:
    user: AppUser
    tenant: Tenant
    role: str
    # Optional only for internal service/unit-test contexts. HTTP authentication
    # always populates this from a validated, database-backed session.
    session: AuthSession | None = None


async def get_auth_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    secret = session_secret_from_request(request)
    if not secret:
        raise HTTPException(status_code=401, detail="Authentication required")
    now = datetime.utcnow()
    auth_session = await db.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(secret, purpose="session"))
    )
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or auth_session.expires_at <= now
    ):
        raise HTTPException(status_code=401, detail="Session is no longer valid")

    membership = await db.scalar(
        select(TenantMember).where(
            TenantMember.user_id == auth_session.user_id,
            TenantMember.tenant_id == auth_session.tenant_id,
        )
    )
    user = await db.get(AppUser, auth_session.user_id)
    tenant = await db.get(Tenant, auth_session.tenant_id)

    if not membership or not user or not user.active or not tenant:
        raise HTTPException(status_code=401, detail="Access denied")

    if auth_session.last_activity_at < now - timedelta(minutes=5):
        auth_session.last_activity_at = now
        await db.commit()

    return AuthContext(
        user=user,
        tenant=tenant,
        role=membership.role,
        session=auth_session,
    )
