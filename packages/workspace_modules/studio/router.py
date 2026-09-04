from __future__ import annotations

import mimetypes
import os
from functools import lru_cache
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db
from packages.database.product_models import SolutionDeployment


public_router = APIRouter(tags=["published-studio"])
# The Studio publisher enforces the same limit before writing a deployment. Keeping
# the serving-side limit aligned prevents a legacy/mutated artifact from turning one
# public GET into an unbounded filesystem walk.
MAX_PUBLISHED_FILES = 1_000
MAX_PUBLISHED_MANIFESTS = 128

# Published/model-authored software is untrusted content. Even on the dedicated
# content origin, keep an opaque-origin CSP as defense in depth so generated scripts
# do not gain ambient origin authority over storage or future privileged endpoints.
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


def _production() -> bool:
    return os.getenv("OPERLY_ENV", os.getenv("APP_ENV", "development")).lower() in {
        "production",
        "prod",
    }


def _studio_public_host() -> str:
    host = os.getenv("OPERLY_STUDIO_PUBLIC_HOST", "").strip().lower().rstrip(".")
    if host and any(token in host for token in ("/", "@", "?", "#", ":")):
        raise HTTPException(status_code=503, detail="Operly Studio content host is invalid")
    return host


def _safe_relative_path(raw: str) -> str:
    value = str(raw or "index.html").replace("\\", "/").lstrip("/") or "index.html"
    if len(value.encode("utf-8")) > 2048:
        raise HTTPException(status_code=404, detail="Published Studio file not found")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(status_code=404, detail="Published Studio file not found")
    return path.as_posix()


def _sites_root() -> Path:
    configured = os.getenv("OPERLY_DEPLOYMENT_ROOT", "").strip()
    if not configured:
        raise HTTPException(status_code=503, detail="Operly Hosting storage is unavailable")
    return (Path(configured).expanduser().resolve() / "studio-sites").resolve()


@lru_cache(maxsize=MAX_PUBLISHED_MANIFESTS)
def _artifact_files(artifact: Path) -> dict[str, Path]:
    """Enumerate one immutable deployment into a bounded trusted file manifest.

    The request path is never joined into a filesystem expression; it is only used as
    a dictionary key against this cached manifest. Filesystem paths come exclusively
    from the already-confined artifact directory. Published artifacts are immutable by
    design, while `_published_candidate` still re-resolves the selected entry before
    every response so an unexpected post-cache symlink mutation cannot escape.
    """

    files: dict[str, Path] = {}
    for directory, dirnames, filenames in os.walk(artifact, followlinks=False):
        directory_path = Path(directory)
        # Never descend through directory symlinks, even when they point back inside.
        dirnames[:] = [
            name for name in dirnames if not (directory_path / name).is_symlink()
        ]
        for name in filenames:
            source = directory_path / name
            try:
                resolved = source.resolve(strict=True)
            except (FileNotFoundError, OSError):
                continue
            if artifact not in resolved.parents or not resolved.is_file():
                continue
            relative = resolved.relative_to(artifact).as_posix()
            files[relative] = resolved
            if len(files) > MAX_PUBLISHED_FILES:
                raise HTTPException(
                    status_code=503,
                    detail="Published Studio artifact exceeds file-count policy",
                )
    return files


def _published_candidate(artifact: Path, request_path: str) -> Path:
    relative = _safe_relative_path(request_path)
    files = _artifact_files(artifact)
    candidate = files.get(relative)
    if candidate is None and not PurePosixPath(relative).suffix:
        candidate = files.get("index.html")
    if candidate is None:
        raise HTTPException(status_code=404, detail="Published Studio file not found")

    # Cached manifests are an optimization, never an authority boundary. Re-resolve
    # the selected trusted entry at response time to catch deletion, replacement, or a
    # symlink introduced after the manifest was built.
    try:
        current = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise HTTPException(status_code=404, detail="Published Studio file not found")
    if artifact not in current.parents or not current.is_file():
        raise HTTPException(status_code=404, detail="Published Studio file not found")
    return current


def _enforce_content_origin(request: Request):
    """Never return generated bytes from Operly's authenticated application origin."""

    studio_host = _studio_public_host()
    if not studio_host:
        if _production():
            raise HTTPException(
                status_code=503,
                detail="Published Studio content is disabled until OPERLY_STUDIO_PUBLIC_HOST is configured",
            )
        return None

    request_host = str(request.url.hostname or "").strip().lower().rstrip(".")
    if request_host == studio_host:
        return None

    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(
        url=f"https://{studio_host}{request.url.path}{query}",
        status_code=307,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@public_router.get("/studio-sites/{solution_id}/", include_in_schema=False)
@public_router.get("/studio-sites/{solution_id}/{path:path}", include_in_schema=False)
async def published_studio_solution(
    request: Request,
    solution_id: str,
    path: str = "index.html",
    db: AsyncSession = Depends(get_db),
):
    redirect = _enforce_content_origin(request)
    if redirect is not None:
        return redirect

    deployment = await db.scalar(
        select(SolutionDeployment)
        .where(
            SolutionDeployment.solution_id == solution_id,
            SolutionDeployment.status == "active",
            SolutionDeployment.health_state == "healthy",
            SolutionDeployment.provider == "operly_static",
        )
        .order_by(desc(SolutionDeployment.deployed_at), desc(SolutionDeployment.created_at))
        .limit(1)
    )
    if deployment is None:
        raise HTTPException(status_code=404, detail="Published Studio Solution not found")

    root = _sites_root()
    artifact = Path(deployment.artifact_reference).resolve()
    if root not in artifact.parents or not artifact.is_dir():
        raise HTTPException(status_code=503, detail="Published Studio artifact is unavailable")

    candidate = _published_candidate(artifact, path)
    media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=60",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "cross-origin",
    }
    if media_type == "text/html":
        headers["Content-Security-Policy"] = PUBLISHED_STUDIO_CSP
    return FileResponse(candidate, media_type=media_type, headers=headers)
