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


def _normalized_origin(value: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, hostname, port


def _request_origin(request: Request) -> tuple[str, str, int | None] | None:
    # TrustedHostMiddleware validates the Host header before application routes.
    # Compare the browser Origin to the host that actually received this request
    # instead of assuming PUBLIC_BASE_URL is the only valid deployment hostname.
    host_header = request.headers.get("host", "").strip()
    if not host_header:
        return None
    try:
        parsed = urlparse(f"//{host_header}")
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return None
    if not hostname:
        return None

    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    scheme = forwarded_proto if forwarded_proto in {"http", "https"} else request.url.scheme.lower()
    if scheme not in {"http", "https"}:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, hostname, port


def _cross_site(request: Request) -> bool:
    fetch_site = request.headers.get("sec-fetch-site", "").lower()
    if fetch_site == "cross-site":
        return True

    origin = request.headers.get("origin")
    if not origin:
        return False

    browser_origin = _normalized_origin(origin)
    request_origin = _request_origin(request)
    if browser_origin and request_origin and browser_origin == request_origin:
        return False

    # Also accept the explicitly configured canonical origin. This preserves
    # custom-domain deployments while allowing same-origin Railway preview/copy
    # hosts to authenticate without changing PUBLIC_BASE_URL first.
    configured = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    configured_origin = _normalized_origin(configured)
    return not (browser_origin and configured_origin and browser_origin == configured_origin)


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
