import os
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        response.headers["Cross-Origin-Opener-Policy"]="same-origin-allow-popups"
        path=request.url.path
        studio_preview=(path.startswith("/apps/") and path.endswith("/preview")) or (path.startswith("/api/studio/projects/") and path.endswith("/preview")) or (path.startswith("/api/custom-software/projects/") and path.endswith("/preview")) or path.startswith("/api/custom-software/previews/") or path.startswith("/api/coding-harness/sources/")
        response.headers["X-Frame-Options"]="SAMEORIGIN" if studio_preview else "DENY"
        frame_ancestors="'self'" if studio_preview else "'none'"
        script_sources="'self' 'unsafe-inline'" if studio_preview else "'self'"
        google_script=" https://accounts.google.com/gsi/client" if not studio_preview else ""
        response.headers["Content-Security-Policy"]=f"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src {script_sources}{google_script}; connect-src 'self' https://accounts.google.com/gsi/; frame-src https://accounts.google.com/gsi/; frame-ancestors {frame_ancestors}; object-src 'none'; base-uri 'self'; form-action 'self'; upgrade-insecure-requests"
        if path.startswith("/api/auth/") or path in {"/api/me","/login","/signup","/verify-email","/forgot-password","/reset-password","/onboarding"}:
            response.headers["Cache-Control"]="no-store, max-age=0"
            response.headers["Pragma"]="no-cache"
        environment=os.getenv("OPERLY_ENV",os.getenv("APP_ENV","development")).lower()
        if environment in {"production","prod"} and request.url.scheme=="https":
            response.headers["Strict-Transport-Security"]="max-age=31536000; includeSubDomains"
        return response
