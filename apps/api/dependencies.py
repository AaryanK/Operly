from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth_cookies import session_secret_from_request
from apps.api.security import hash_token
from packages.database.db import SessionFactory
from packages.database.model_trace import ensure_model_trace_sink
from packages.database.models import AppUser, AuthSession, Tenant, TenantMember
from packages.model_runtime.trace_context import runtime_trace_scope


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
) -> AsyncIterator[AccountAuthContext]:
    """Authenticate the account without requiring a workspace membership.

    Personal AI, account settings, session management, and workspace discovery use
    this dependency. Workspace/business routes must continue through
    ``get_auth_context`` so a personal session never implicitly gains workspace
    authority.

    The dependency also establishes a request-level model trace scope. Any shared
    model-runtime invocation beneath this authenticated request therefore receives a
    stable run/conversation correlation even when the individual callsite did not
    explicitly opt into AgentRuntime. More-specific agent/Studio scopes may override
    these synthetic HTTP identifiers.
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

    # Install once per process before entering the ambient correlation scope. This
    # makes non-AgentRuntime model callsites observable too; registration is
    # idempotent and tracing failures never break inference.
    ensure_model_trace_sink()
    context = AccountAuthContext(user=user, session=auth_session)
    request_run_id = str(uuid4())
    request_trace = {
        "runtime_run_id": request_run_id,
        "conversation_id": f"http:{request_run_id}",
        "user_id": user.id,
        "principal_id": f"user:{user.id}",
        "channel": "web",
        # Account-scoped work is private by default. A workspace dependency below
        # this scope explicitly upgrades the trace to workspace/request after live
        # membership has been verified.
        "surface": "private/direct",
        "runtime_component": f"http:{request.method.lower()}:{request.url.path}",
        "http_path": request.url.path,
        "http_method": request.method,
    }
    with runtime_trace_scope(request_trace):
        yield context


async def get_auth_context(
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
) -> AsyncIterator[AuthContext]:
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

    context = AuthContext(
        user=account.user,
        tenant=tenant,
        role=membership.role,
        session=account.session,
    )
    with runtime_trace_scope(
        {
            "tenant_id": tenant.id,
            "principal_id": f"web-user:{account.user.id}",
            "channel": "web",
            "surface": "workspace/request",
        }
    ):
        yield context
