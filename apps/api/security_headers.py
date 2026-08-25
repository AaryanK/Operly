import os
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse


_FRONTEND_SHELL_PATHS = {
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
    "/channels",
}


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
    origins=_runner_preview_origins()
    if not origins:
        return "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    delegated=" ".join(f'"{origin}"' for origin in origins)
    return f"camera=({delegated}), microphone=({delegated}), geolocation=({delegated}), payment=({delegated}), usb=({delegated})"


def _is_known_frontend_fallback(path: str) -> bool:
    normalized=path.rstrip("/") or "/"
    if normalized in _FRONTEND_SHELL_PATHS:
        return True
    if normalized.startswith("/channels/"):
        return True
    if normalized.startswith("/assets/"):
        return True
    segment=normalized.rsplit("/",1)[-1]
    return "." in segment and not segment.startswith(".")


def _unknown_frontend_fallback(request) -> bool:
    route=request.scope.get("route")
    if getattr(route,"name",None)!="frontend":
        return False
    return not _is_known_frontend_fallback(request.url.path)


def _not_found_response(path: str) -> HTMLResponse:
    safe_path=(
        path.replace("&","&amp;")
        .replace("<","&lt;")
        .replace(">","&gt;")
        .replace('"',"&quot;")
    )
    return HTMLResponse(
        f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Page not found · OPERLY</title><style>html{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:28px;background:radial-gradient(circle at 18% 10%,#342d63 0,transparent 34%),linear-gradient(145deg,#12111d,#19172a);color:#f5f2ff;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif}}main{{width:min(680px,100%);padding:38px;border:1px solid #39334f;border-radius:24px;background:rgba(29,26,43,.92);box-shadow:0 28px 90px rgba(0,0,0,.32)}}span{{display:inline-block;padding:6px 10px;border-radius:999px;background:#2d2847;color:#c9c0ff;font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}}h1{{margin:18px 0 10px;font-size:clamp(38px,8vw,70px);letter-spacing:-.055em}}p{{margin:0;color:#aaa3bc;line-height:1.65}}code{{display:block;margin:20px 0;padding:12px 14px;border:1px solid #3b3650;border-radius:12px;background:#15131f;color:#d9d3ee;overflow-wrap:anywhere}}a{{display:inline-flex;margin-top:10px;padding:11px 16px;border-radius:11px;background:#7667f5;color:#fff;text-decoration:none;font-weight:800}}</style></head><body><main><span>404 · Not found</span><h1>This page isn’t here.</h1><p>The address does not match an Operly route. Nothing was loaded behind this URL.</p><code>{safe_path}</code><a href=\"/\">Go to Operly</a></main></body></html>""",
        status_code=404,
        headers={"Cache-Control":"no-store, max-age=0","X-Robots-Tag":"noindex"},
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        response=await call_next(request)
        if request.method in {"GET","HEAD"} and _unknown_frontend_fallback(request):
            response=_not_found_response(request.url.path)
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
        if path.startswith("/api/auth/") or path.startswith("/api/admin/") or path in {"/api/me","/login","/signup","/verify-email","/forgot-password","/reset-password","/onboarding","/admin"}:
            response.headers["Cache-Control"]="no-store, max-age=0"
            response.headers["Pragma"]="no-cache"
        environment=os.getenv("OPERLY_ENV",os.getenv("APP_ENV","development")).lower()
        if environment in {"production","prod"} and request.url.scheme=="https":
            response.headers["Strict-Transport-Security"]="max-age=31536000; includeSubDomains"
        return response
