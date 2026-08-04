from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
EXEMPT_PATHS = {
    "/api/session/login",
    "/api/health",
}


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if (
            request.method in SAFE_METHODS
            or request.url.path in EXEMPT_PATHS
            or request.url.path.startswith("/api/public/")
            or not request.url.path.startswith("/api/")
        ):
            return await call_next(request)

        session_cookie = request.cookies.get("operly_session")
        if not session_cookie:
            # Bearer-token API clients are not cookie-authenticated and are
            # not vulnerable to browser CSRF in the same way.
            return await call_next(request)

        cookie_token = request.cookies.get("operly_csrf")
        header_token = request.headers.get("x-csrf-token")

        if (
            not cookie_token
            or not header_token
            or cookie_token != header_token
        ):
            return JSONResponse(
                {"detail": "CSRF validation failed"},
                status_code=403,
            )

        return await call_next(request)
