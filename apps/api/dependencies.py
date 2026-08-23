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
class AccountAuthContext:
    """Authenticated person independent of any selected workspace."""

    user: AppUser
    session: AuthSession

    @property
    def tenant_id(self) -> str | None:
        return self.session.tenant_id


@dataclass(slots=True)
class AuthContext:
    """Authenticated person with a currently selected, authorized workspace."""

    user: AppUser
    tenant: Tenant
    role: str
    # Optional only for internal service/unit-test contexts. HTTP authentication
    # always populates this from a validated, database-backed session.
    session: AuthSession | None = None


async def get_account_auth_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AccountAuthContext:
    """Authenticate the account without requiring a workspace membership.

    Personal AI, account settings, session management, and workspace discovery use
    this dependency. Workspace/business routes must continue through
    ``get_auth_context`` so a personal session never implicitly gains workspace
    authority.
    """

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

    user = await db.get(AppUser, auth_session.user_id)
    if not user or not user.active:
        raise HTTPException(status_code=401, detail="Access denied")

    if auth_session.last_activity_at < now - timedelta(minutes=5):
        auth_session.last_activity_at = now
        await db.commit()

    return AccountAuthContext(user=user, session=auth_session)


async def get_auth_context(
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """Require an explicitly selected workspace and a live membership."""

    tenant_id = account.session.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "WORKSPACE_REQUIRED",
                "message": "Select or create a workspace for this workspace-scoped operation.",
            },
        )

    membership = await db.scalar(
        select(TenantMember).where(
            TenantMember.user_id == account.user.id,
            TenantMember.tenant_id == tenant_id,
        )
    )
    tenant = await db.get(Tenant, tenant_id)
    if not membership or not tenant:
        raise HTTPException(status_code=403, detail="Workspace access denied")

    return AuthContext(
        user=account.user,
        tenant=tenant,
        role=membership.role,
        session=account.session,
    )
