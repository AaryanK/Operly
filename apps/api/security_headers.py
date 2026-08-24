import os
from starlette.middleware.base import BaseHTTPMiddleware


def _runner_preview_origins() -> tuple[str, ...]:
    origins=[]
    for value in os.getenv("OPERLY_SANDBOX_PREVIEW_HOSTS","").split(","):
        host=value.strip().lower()
        if not host or any(token in host for token in ("/","@","?","#")):
            continue
        origins.append(f"https://{host}")
    return tuple(dict.fromkeys(origins))


def _permissions_policy(solution_studio: bool) -> str:
    if not solution_studio:
        return "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    delegated=" ".join(["self",*(f'"{origin}"' for origin in _runner_preview_origins())])
    return f"camera=({delegated}), microphone=({delegated}), geolocation=({delegated}), payment=({delegated}), usb=({delegated})"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
        path=request.url.path
        solution_studio=path.startswith("/channels/") and path.endswith("/solutions")
        response.headers["Permissions-Policy"]=_permissions_policy(solution_studio)
        response.headers["Cross-Origin-Opener-Policy"]="same-origin-allow-popups"
        solution_preview=path.startswith("/api/solutions/") and path.endswith("/preview")
        source_preview=path.startswith("/api/studio/projects/") and "/source/preview" in path
        generated_preview=path.startswith("/api/custom-software/previews/")
        studio_preview=(path.startswith("/apps/") and path.endswith("/preview")) or (path.startswith("/api/studio/projects/") and path.endswith("/preview")) or source_preview or (path.startswith("/api/custom-software/projects/") and path.endswith("/preview")) or generated_preview or path.startswith("/api/coding-harness/sources/") or solution_preview
        response.headers["X-Frame-Options"]="SAMEORIGIN" if studio_preview else "DENY"

        if source_preview:
            # Model-authored source is rendered only inside Studio's sandboxed iframe.
            # It may render and run local progressive-enhancement JS, but it cannot
            # connect back to Operly APIs or embed privileged same-origin resources.
            response.headers["Content-Security-Policy"]="default-src 'none'; img-src 'self' data: https:; font-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'none'; frame-src 'none'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'; form-action 'none'"
        else:
            frame_ancestors="'self'" if studio_preview else "'none'"
            script_sources="'self' 'unsafe-inline'" if studio_preview else "'self'"
            google_script=" https://accounts.google.com/gsi/client" if not studio_preview else ""
            image_sources="'self' data: https:" if studio_preview else "'self' data:"
            runner_frames=" ".join(_runner_preview_origins()) if solution_studio else ""
            frame_sources=f"'self' https://accounts.google.com/gsi/ {runner_frames}".strip()
            response.headers["Content-Security-Policy"]=f"default-src 'self'; img-src {image_sources}; style-src 'self' 'unsafe-inline'; script-src {script_sources}{google_script}; connect-src 'self' https://accounts.google.com/gsi/; frame-src {frame_sources}; frame-ancestors {frame_ancestors}; object-src 'none'; base-uri 'self'; form-action 'self'; upgrade-insecure-requests"
        if path.startswith("/api/auth/") or path in {"/api/me","/login","/signup","/verify-email","/forgot-password","/reset-password","/onboarding"}:
            response.headers["Cache-Control"]="no-store, max-age=0"
            response.headers["Pragma"]="no-cache"
        environment=os.getenv("OPERLY_ENV",os.getenv("APP_ENV","development")).lower()
        if environment in {"production","prod"} and request.url.scheme=="https":
            response.headers["Strict-Transport-Security"]="max-age=31536000; includeSubDomains"
        return response
