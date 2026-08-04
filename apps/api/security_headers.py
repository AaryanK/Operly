import os
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
        path=request.url.path
        studio_preview=(path.startswith("/apps/") and path.endswith("/preview")) or (path.startswith("/api/studio/projects/") and path.endswith("/preview"))
        response.headers["X-Frame-Options"]="SAMEORIGIN" if studio_preview else "DENY"
        frame_ancestors="'self'" if studio_preview else "'none'"
        script_sources="'self' 'unsafe-inline'" if studio_preview else "'self'"
        response.headers["Content-Security-Policy"]=f"default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src {script_sources}; connect-src 'self'; frame-ancestors {frame_ancestors}; object-src 'none'; base-uri 'self'; form-action 'self'"
        environment=os.getenv("OPERLY_ENV",os.getenv("APP_ENV","development")).lower()
        if environment in {"production","prod"} and request.url.scheme=="https":
            response.headers["Strict-Transport-Security"]="max-age=31536000"
        return response
