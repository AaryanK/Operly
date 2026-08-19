import hmac
import os
from urllib.parse import urlparse

from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from apps.api.auth_cookies import (
    PREAUTH_CSRF_COOKIE,
    csrf_secret_from_request,
    session_secret_from_request,
)
from apps.api.security import hash_token
from packages.database.db import SessionFactory
from packages.database.models import AuthSession


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PREAUTH_PATHS = {
    "/api/auth/signup",
    "/api/auth/login",
    "/api/session/login",
    "/api/auth/verify-email",
    "/api/auth/resend-verification",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/auth/google",
}


def _failure(code: str = "CSRF_VALIDATION_FAILED") -> JSONResponse:
    return JSONResponse(
        {"detail": {"code": code, "message": "Your secure session could not be confirmed. Please try again."}},
        status_code=403,
        headers={"Cache-Control": "no-store"},
    )


def _cross_site(request: Request) -> bool:
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return True
    origin = request.headers.get("origin")
    if not origin:
        return False
    configured = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    return origin.rstrip("/") != configured


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            request.method in SAFE_METHODS
            or not path.startswith("/api/")
            or path == "/api/health"
            or path.startswith("/api/public/")
        ):
            return await call_next(request)

        if path.startswith("/api/auth/") and request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            return JSONResponse(
                {"detail": {"code": "JSON_REQUIRED", "message": "Send this request as JSON."}},
                status_code=415,
                headers={"Cache-Control": "no-store"},
            )

        if _cross_site(request):
            return _failure("CROSS_SITE_REQUEST_REJECTED")

        header_token = request.headers.get("x-csrf-token")
        session_secret = session_secret_from_request(request)
        if session_secret:
            cookie_token = csrf_secret_from_request(request)
            if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
                if path in PREAUTH_PATHS:
                    preauth = request.cookies.get(PREAUTH_CSRF_COOKIE)
                    if preauth and header_token and hmac.compare_digest(preauth, header_token):
                        return await call_next(request)
                return _failure()
            token_hash = hash_token(session_secret, purpose="session")
            csrf_hash = hash_token(header_token, purpose="csrf")
            async with SessionFactory() as db:
                auth_session = await db.scalar(
                    select(AuthSession).where(
                        AuthSession.token_hash == token_hash,
                        AuthSession.csrf_token_hash == csrf_hash,
                        AuthSession.revoked_at.is_(None),
                    )
                )
            if auth_session is None:
                # A stale or attacker-supplied non-authoritative cookie must
                # not become a session-fixation primitive. Login may replace
                # it only when the independent pre-authentication proof is
                # valid.
                if path in PREAUTH_PATHS:
                    preauth = request.cookies.get(PREAUTH_CSRF_COOKIE)
                    if preauth and header_token and hmac.compare_digest(preauth, header_token):
                        return await call_next(request)
                return _failure()
            return await call_next(request)

        if path in PREAUTH_PATHS:
            cookie_token = request.cookies.get(PREAUTH_CSRF_COOKIE)
            if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
                return _failure()

        return await call_next(request)
