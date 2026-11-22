"""Operly is a relying-party client of DragonZpyder's identity authority.

Once identity cutover is explicitly enabled, Operly refuses to register or
password-authenticate independent local users. A local AppUser row remains an
immutable, migration-audited projection for Workspace membership FKs; it is
NOT an independent credential source. DragonZpyder OIDC sign-in remains open.
"""
from __future__ import annotations

import os

from fastapi.responses import JSONResponse

LOCAL_ACCOUNT_PATHS = frozenset({
    "/api/auth/signup",
    "/api/auth/login",
    "/api/session/login",
    "/api/auth/google",
    "/api/auth/verify-email",
    "/api/auth/resend-verification",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/auth/change-password",
    "/api/identities/discord/sign-in",
    "/api/identities/discord/callback",
})


def identity_owned_by_dragonzpyder() -> bool:
    return os.getenv("OPERLY_IDENTITY_OWNER") == "dragonzpyder"


def deny_local_identity_route(path: str) -> bool:
    # Do not catch OIDC relying-party routes, workspace membership, OAuth
    # MCP permissions, or business connector routes.
    return identity_owned_by_dragonzpyder() and path in LOCAL_ACCOUNT_PATHS


async def identity_owner_middleware(request, call_next):
    if deny_local_identity_route(request.url.path):
        return JSONResponse(
            status_code=410, content={"detail": {
                "code": "LOCAL_OPERLY_IDENTITY_RETIRED",
                "message": "Sign in with DragonZpyder to access Operly workspaces.",
            }}, headers={"Cache-Control": "no-store"},
        )
    return await call_next(request)
