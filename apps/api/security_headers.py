import os
from pathlib import Path
from urllib.parse import unquote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse


PUBLISHED_STUDIO_CSP = (
    "sandbox allow-scripts allow-forms allow-modals allow-popups allow-downloads; "
    "default-src 'self' https: data: blob:; "
    "img-src 'self' https: data: blob:; "
    "style-src 'self' 'unsafe-inline' https:; "
    "script-src 'self' 'unsafe-inline' https:; "
    "font-src 'self' https: data:; "
    "connect-src https:; "
    "frame-src https:; "
    "worker-src 'self' blob:; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action https:; "
    "upgrade-insecure-requests"
)


def _runner_preview_origins() -> tuple[str, ...]:
    origins=[]
    for value in os.getenv("OPERLY_SANDBOX_PREVIEW_HOSTS","").split(","):
        host=value.strip().lower()
        if not host or any(token in host for token in ("/","@","?","#")):
            continue
        origins.append(f"https://{host}")
    return tuple(dict.fromkeys(origins))


def _studio_public_host() -> str:
    host = os.getenv("OPERLY_STUDIO_PUBLIC_HOST", "").strip().lower().rstrip(".")
    if host and any(token in host for token in ("/", "@", "?", "#", ":")):
        return ""
    return host


def _permissions_policy(solution_studio: bool) -> str:
    if not solution_studio:
        return "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    origins=_runner_preview_origins()
    if not origins:
        return "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    delegated=" ".join(f'"{origin}"' for origin in origins)
    return f"camera=({delegated}), microphone=({delegated}), geolocation=({delegated}), payment=({delegated}), usb=({delegated})"


def confined_file(root: Path, relative_path: str) -> Path | None:
    """Resolve an existing file only when its real path stays under ``root``.

    ``Path.resolve`` follows symlinks, so this is a sink-level containment check in
    addition to request-path validation. A file symlinked out of the frontend build
    directory is therefore not servable by the catch-all route.
    """

    resolved_root = Path(root).resolve()
    candidate = (resolved_root / str(relative_path or "")).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _unsafe_request_path(request) -> bool:
    """Reject traversal before any catch-all/static-file route can touch disk.

    ASGI servers and proxies do not all normalize encoded path separators in the
    same order. Check both Starlette's decoded path and the raw path, repeatedly
    decoding a small bounded number of times so double-encoded dot segments cannot
    reach filesystem-backed routes.
    """

    raw_path = request.scope.get("raw_path", b"")
    if isinstance(raw_path, bytes):
        raw_text = raw_path.decode("latin-1", "ignore")
    else:
        raw_text = str(raw_path or "")
    for candidate in (request.url.path, raw_text):
        current = str(candidate or "")
        for _ in range(4):
            if "\x00" in current:
                return True
            normalized = current.replace("\\", "/")
            if any(segment == ".." for segment in normalized.split("/")):
                return True
            decoded = unquote(current)
            if decoded == current:
                break
            current = decoded
    return False


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        path=request.url.path
        request_host=str(request.url.hostname or "").strip().lower().rstrip(".")
        studio_host=_studio_public_host()
        if studio_host and request_host == studio_host:
            # The generated-content hostname is a deliberately tiny origin. It must
            # never expose Operly UI/API/auth routes even though DNS may point to the
            # same deployment. Only immutable-ish published-site GET/HEAD requests
            # reach routing; everything else fails closed before session/CSRF logic.
            if request.method.upper() not in {"GET", "HEAD"} or not path.startswith("/studio-sites/"):
                return PlainTextResponse(
                    "Not Found",
                    status_code=404,
                    headers={
                        "Cache-Control": "no-store",
                        "X-Content-Type-Options": "nosniff",
                        "Referrer-Policy": "no-referrer",
                    },
                )
        if _unsafe_request_path(request):
            return PlainTextResponse(
                "Bad Request",
                status_code=400,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )
        response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
        solution_studio=path.startswith("/channels/") and path.endswith("/solutions")
        response.headers["Permissions-Policy"]=_permissions_policy(solution_studio)
        response.headers["Cross-Origin-Opener-Policy"]="same-origin-allow-popups"
        solution_preview=path.startswith("/api/solutions/") and path.endswith("/preview")
        source_preview=path.startswith("/api/studio/projects/") and "/source/preview" in path
        generated_preview=path.startswith("/api/custom-software/previews/")
        hosted_plugin=path.startswith("/api/public/plugins/")
        published_studio=path.startswith("/studio-sites/")
        studio_preview=(path.startswith("/apps/") and path.endswith("/preview")) or (path.startswith("/api/studio/projects/") and path.endswith("/preview")) or source_preview or (path.startswith("/api/custom-software/projects/") and path.endswith("/preview")) or generated_preview or path.startswith("/api/coding-harness/sources/") or solution_preview
        response.headers["X-Frame-Options"]="SAMEORIGIN" if (studio_preview or hosted_plugin) else "DENY"

        if published_studio:
            response.headers["Content-Security-Policy"]=PUBLISHED_STUDIO_CSP
            response.headers["Referrer-Policy"]="no-referrer"
            response.headers["Cross-Origin-Resource-Policy"]="cross-origin"
        elif hosted_plugin:
            # Workspace plugin UI is untrusted authored content. It may be framed by
            # Operly, but CSP sandbox deliberately gives it an opaque origin even when
            # opened standalone so it cannot inherit Operly cookies/local storage or
            # call authenticated Workspace APIs directly. A governed UI bridge can be
            # added later without weakening this default boundary.
            response.headers["Content-Security-Policy"]="sandbox allow-scripts allow-forms; default-src 'none'; img-src 'self' data: https:; font-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'none'; frame-src 'none'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; upgrade-insecure-requests"
        elif source_preview:
            # Model-authored source is rendered only inside Studio's sandboxed iframe.
            # It may render and run local progressive-enhancement JS, but it cannot
            # connect back to Operly APIs or embed privileged same-origin resources.
            response.headers["Content-Security-Policy"]="default-src 'none'; img-src 'self' data: https:; font-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'none'; frame-src 'none'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'; form-action 'none'"
        else:
            frame_ancestors="'self'" if studio_preview else "'none'"
            script_sources="'self' 'unsafe-inline'" if studio_preview else "'self'"
            google_script=" https://accounts.google.com/gsi/client" if not studio_preview else ""
            provider_images="https://cdn.discordapp.com https://lh3.googleusercontent.com"
            image_sources="'self' data: https:" if studio_preview else f"'self' data: {provider_images}"
            runner_frames=" ".join(_runner_preview_origins()) if solution_studio else ""
            frame_sources=f"'self' https://accounts.google.com/gsi/ {runner_frames}".strip()
            response.headers["Content-Security-Policy"]=f"default-src 'self'; img-src {image_sources}; style-src 'self' 'unsafe-inline'; script-src {script_sources}{google_script}; connect-src 'self' https://accounts.google.com/gsi/; frame-src {frame_sources}; frame-ancestors {frame_ancestors}; object-src 'none'; base-uri 'self'; form-action 'self'; upgrade-insecure-requests"
        if path.startswith("/api/auth/") or path.startswith("/api/admin/") or path in {"/api/me","/login","/signup","/verify-email","/forgot-password","/reset-password","/onboarding","/admin"}:
            response.headers["Cache-Control"]="no-store, max-age=0"
            response.headers["Pragma"]="no-cache"
        environment=os.getenv("OPERLY_ENV",os.getenv("APP_ENV","development")).lower()
        if environment in {"production","prod"} and request.url.scheme=="https":
            response.headers["Strict-Transport-Security"]="max-age=31536000; includeSubDomains"
        return response