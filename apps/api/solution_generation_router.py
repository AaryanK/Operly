import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.coding_harness.source_service import source_record_json
from packages.database.custom_software_models import GeneratedProject, GeneratedSourceBundle
from packages.software_projects.source_service import SoftwareSourceError, SoftwareSourceService, files_from_row
from packages.solutions.composer import retry_solution_initial_generation
from packages.solutions.service import RuntimeType, SolutionService, solution_json


router = APIRouter(prefix="/api/solutions", tags=["solutions"])
service = SolutionService()
software_source_service = SoftwareSourceService()


async def _latest_generated_source(
    db: AsyncSession,
    tenant_id: str,
    solution,
) -> GeneratedSourceBundle | None:
    """Resolve the newest persisted source bundle behind one legacy generated Solution."""
    if solution.runtime_type != RuntimeType.GENERATED_PROJECT:
        return None
    project = await db.get(GeneratedProject, solution.runtime_reference)
    if project is None or project.tenant_id != tenant_id or not project.plan_id:
        return None

    query = select(GeneratedSourceBundle).where(
        GeneratedSourceBundle.tenant_id == tenant_id,
        GeneratedSourceBundle.plan_id == project.plan_id,
    )
    if project.approved_plan_version:
        query = query.where(
            GeneratedSourceBundle.plan_version == project.approved_plan_version,
        )
    return await db.scalar(query.order_by(desc(GeneratedSourceBundle.source_version)).limit(1))


def _source_inspector_json(source: GeneratedSourceBundle) -> dict:
    """Return legacy generated source text for an authenticated workspace inspector."""
    try:
        records = json.loads(source.files_json or "[]")
    except Exception as error:
        raise ValueError("Stored generated source is invalid") from error
    if not isinstance(records, list):
        raise ValueError("Stored generated source is invalid")

    files = []
    for item in records:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        content = item.get("content")
        if not path or not isinstance(content, str):
            continue
        files.append(
            {
                "path": path,
                "content": content,
                "generatedBy": str(item.get("generatedBy") or "coding_harness"),
                "sizeBytes": len(content.encode("utf-8")),
            }
        )
    files.sort(key=lambda item: item["path"])

    result = source_record_json(source)
    # source_record_json exposes manifest file metadata. The inspector endpoint
    # intentionally replaces that list with the authenticated owner's persisted
    # UTF-8 source text so Studio/Solutions can render a read-only file explorer.
    result["files"] = files
    result["fileCount"] = len(files)
    return result


def _canonical_source_inspector_json(source) -> dict:
    """Project canonical SoftwareProject source into the same read-only inspector shape."""
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
    """Return the runtime-neutral capability graph for a composed Solution."""
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
        raise HTTPException(
            status_code=404,
            detail="This legacy Solution does not have a capability manifest yet",
        )
    return manifest


@router.get("/{solution_id}/source")
async def solution_source(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Inspect the newest immutable authoritative source without executing it."""
    try:
        row = await service.get(db, auth.tenant.id, solution_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    if row.runtime_type == RuntimeType.SOFTWARE_PROJECT:
        source = await software_source_service.latest(db, auth.tenant.id, row.runtime_reference)
        if source is None:
            raise HTTPException(
                status_code=404,
                detail="Canonical source is not available for this Solution yet",
            )
        try:
            return _canonical_source_inspector_json(source)
        except SoftwareSourceError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error

    source = await _latest_generated_source(db, auth.tenant.id, row)
    if source is None:
        raise HTTPException(
            status_code=404,
            detail="Generated source is not available for this Solution yet",
        )
    try:
        return _source_inspector_json(source)
    except ValueError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.post("/{solution_id}/retry-generation")
async def retry_solution_generation(
    solution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Retry initial generation from the stored owner objective and capability graph."""
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
