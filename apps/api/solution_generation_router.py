import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.software_projects.source_service import SoftwareSourceError, SoftwareSourceService, files_from_row
from packages.solutions.composer import retry_solution_initial_generation
from packages.solutions.service import SolutionService, solution_json


router = APIRouter(prefix="/api/solutions", tags=["solutions"])
service = SolutionService()
software_source_service = SoftwareSourceService()


def _canonical_source_inspector_json(source) -> dict:
    files_by_path = files_from_row(source)
    files = [
        {
            "path": path,
            "content": content,
            "generatedBy": "agent_runtime",
            "sizeBytes": len(content.encode("utf-8")),
        }
        for path, content in sorted(files_by_path.items())
    ]
    return {
        "id": source.id,
        "projectId": source.project_id,
        "sourceVersion": source.source_version,
        "bundleDigest": source.bundle_digest,
        "runtimeProfile": source.runtime_profile,
        "summary": source.change_summary,
        "originatingRunId": source.originating_run_id,
        "files": files,
        "fileCount": len(files),
        "totalBytes": sum(item["sizeBytes"] for item in files),
        "sourceAuthority": "software_source_versions",
    }


@router.get("/{solution_id}/architecture")
async def solution_architecture(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await service.get(db, auth.tenant.id, solution_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    try:
        context = json.loads(row.context_json or "{}")
    except Exception:
        context = {}
    manifest = context.get("solutionManifest") if isinstance(context, dict) else None
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=404, detail="Solution capability manifest is unavailable")
    return manifest


@router.get("/{solution_id}/source")
async def solution_source(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await service.get(db, auth.tenant.id, solution_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    source = await software_source_service.latest(db, auth.tenant.id, row.runtime_reference)
    if source is None:
        raise HTTPException(status_code=404, detail="Canonical source is not available for this Solution yet")
    try:
        return _canonical_source_inspector_json(source)
    except SoftwareSourceError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.post("/{solution_id}/retry-generation")
async def retry_solution_generation(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can retry Solution generation")
    try:
        row = await retry_solution_initial_generation(
            db,
            tenant_id=auth.tenant.id,
            user_id=auth.user.id,
            solution_id=solution_id,
            service=service,
        )
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    await db.commit()
    return {"solution": solution_json(row)}
