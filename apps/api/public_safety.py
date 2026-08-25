"""Bound anonymous request bodies, rate-limit public APIs, and reject unknown site routes."""
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, JSONResponse


PUBLIC_SHELL_ROUTES = {
    "/",
    "/login",
    "/signup",
    "/verify-email",
    "/forgot-password",
    "/reset-password",
    "/onboarding",
    "/privacy",
    "/terms",
    "/admin",
    "/docs",
    "/redoc",
    "/openapi.json",
}
PUBLIC_ROUTE_PREFIXES = (
    "/api/",
    "/static/",
    "/channels/",
    "/apps/",
)


def _looks_like_asset(path: str) -> bool:
    """Let the downstream static-file fallback decide asset existence.

    Extensionless browser routes are the dangerous case: FastAPI's final SPA
    fallback used to return the landing page for any typo, making bogus URLs look
    valid. Asset-shaped paths still pass through so existing first-party files keep
    working and missing assets can be handled by the normal file/router layer.
    """
    segment = path.rsplit("/", 1)[-1]
    return "." in segment and not segment.startswith(".")


def _unknown_public_route(path: str) -> bool:
    if path in PUBLIC_SHELL_ROUTES:
        return False
    if any(path.startswith(prefix) for prefix in PUBLIC_ROUTE_PREFIXES):
        return False
    if _looks_like_asset(path):
        return False
    return True


def _not_found(path: str, accept: str):
    if "application/json" in accept.lower():
        return JSONResponse(
            {"detail": {"code": "NOT_FOUND", "message": "This Operly page does not exist."}},
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )
    safe_path = path.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return HTMLResponse(
        f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Page not found · OPERLY</title><style>html{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:28px;background:radial-gradient(circle at 18% 10%,#342d63 0,transparent 34%),linear-gradient(145deg,#12111d,#19172a);color:#f5f2ff;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif}}main{{width:min(680px,100%);padding:38px;border:1px solid #39334f;border-radius:24px;background:rgba(29,26,43,.9);box-shadow:0 28px 90px rgba(0,0,0,.32)}}span{{display:inline-block;padding:6px 10px;border-radius:999px;background:#2d2847;color:#c9c0ff;font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}}h1{{margin:18px 0 10px;font-size:clamp(38px,8vw,70px);letter-spacing:-.055em}}p{{margin:0;color:#aaa3bc;line-height:1.65}}code{{display:block;margin:20px 0;padding:12px 14px;border:1px solid #3b3650;border-radius:12px;background:#15131f;color:#d9d3ee;overflow-wrap:anywhere}}a{{display:inline-flex;margin-top:10px;padding:11px 16px;border-radius:11px;background:#7667f5;color:white;text-decoration:none;font-weight:800}}</style></head><body><main><span>404 · Not found</span><h1>This page isn’t here.</h1><p>The address does not match an Operly route. Nothing was loaded behind this URL.</p><code>{safe_path}</code><a href=\"/\">Go to Operly</a></main></body></html>""",
        status_code=404,
        headers={"Cache-Control": "no-store, max-age=0", "X-Robots-Tag": "noindex"},
    )


class PublicEndpointSafetyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_body_bytes=1_048_576, requests_per_minute=30):
        super().__init__(app)
        self.max_body_bytes = max_body_bytes
        self.limit = requests_per_minute
        self.hits = defaultdict(deque)

    async def dispatch(self, request, call_next):
        path = request.url.path
        if request.method in {"GET", "HEAD"} and _unknown_public_route(path):
            return _not_found(path, request.headers.get("accept", ""))

        if path.startswith("/api/public/"):
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                length = self.max_body_bytes + 1
            if length > self.max_body_bytes:
                return JSONResponse({"detail": "Request body is too large"}, 413)
            now = time.monotonic()
            key = (request.client.host if request.client else "unknown", path)
            bucket = self.hits[key]
            while bucket and bucket[0] < now - 60:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return JSONResponse({"detail": "Too many anonymous requests"}, 429)
            bucket.append(now)
        return await call_next(request)
