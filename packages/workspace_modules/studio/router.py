from __future__ import annotations

import mimetypes
import os
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db
from packages.database.product_models import SolutionDeployment


public_router = APIRouter(tags=["published-studio"])

# Published/model-authored software is untrusted content even when Operly generated
# it. CSP sandbox without allow-same-origin gives every page an opaque origin, so its
# JavaScript cannot inherit Operly cookies/localStorage or act as an authenticated
# Operly application merely because the asset is currently served by the same host.
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


def _safe_relative_path(raw: str) -> str:
    value = str(raw or "index.html").replace("\\", "/").lstrip("/") or "index.html"
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(status_code=404, detail="Published Studio file not found")
    return str(path)


def _sites_root() -> Path:
    configured = os.getenv("OPERLY_DEPLOYMENT_ROOT", "").strip()
    if not configured:
        raise HTTPException(status_code=503, detail="Operly Hosting storage is unavailable")
    return (Path(configured).expanduser().resolve() / "studio-sites").resolve()


@public_router.get("/studio-sites/{solution_id}/", include_in_schema=False)
@public_router.get("/studio-sites/{solution_id}/{path:path}", include_in_schema=False)
async def published_studio_solution(
    solution_id: str,
    path: str = "index.html",
    db: AsyncSession = Depends(get_db),
):
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

    relative = _safe_relative_path(path)
    candidate = (artifact / relative).resolve()
    if artifact not in candidate.parents:
        raise HTTPException(status_code=404, detail="Published Studio file not found")
    if not candidate.is_file() and not PurePosixPath(relative).suffix:
        candidate = artifact / "index.html"
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Published Studio file not found")

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
