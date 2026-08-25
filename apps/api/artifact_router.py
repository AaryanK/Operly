from __future__ import annotations

import html
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import (
    AccountAuthContext,
    AuthContext,
    get_account_auth_context,
    get_auth_context,
    get_db,
)
from packages.artifacts.service import (
    MAX_ARTIFACT_BYTES,
    ArtifactScope,
    ArtifactService,
    artifact_json,
)


router = APIRouter(tags=["artifacts"])
MAX_UPLOAD_FILES = 50


def _workspace_scope(auth: AuthContext) -> ArtifactScope:
    return ArtifactScope("workspace", auth.tenant.id, tenant_id=auth.tenant.id)


def _personal_scope(auth: AccountAuthContext) -> ArtifactScope:
    return ArtifactScope("personal", f"personal:{auth.user.id}", owner_user_id=auth.user.id)


async def _save_uploads(
    *,
    files: list[UploadFile],
    service: ArtifactService,
    scope: ArtifactScope,
    actor_id: str,
    source: str,
) -> list[dict]:
    if not files:
        raise HTTPException(status_code=422, detail="Attach at least one file")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload at most {MAX_UPLOAD_FILES} files per request; clients may send multiple chunks.",
        )
    output = []
    for upload in files:
        raw = await upload.read(MAX_ARTIFACT_BYTES + 1)
        await upload.close()
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"{upload.filename or 'File'} exceeds the artifact size limit",
            )
        row = await service.create_bytes(
            scope,
            filename=upload.filename or "artifact.bin",
            content_type=upload.content_type,
            content=raw,
            source=source,
            created_by=actor_id,
            metadata={"ingress": "http_multipart_v1"},
        )
        output.append(artifact_json(row))
    return output


@router.post("/artifacts/upload")
async def upload_workspace_artifacts(
    files: list[UploadFile] = File(default=[]),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = await _save_uploads(
        files=files,
        service=ArtifactService(db),
        scope=_workspace_scope(auth),
        actor_id=auth.user.id,
        source="workspace_upload",
    )
    await db.commit()
    return {"artifacts": rows, "artifact_ids": [row["artifact_id"] for row in rows]}


@router.get("/artifacts")
async def list_workspace_artifacts(
    limit: int = Query(default=100, ge=1, le=200),
    run_id: str | None = Query(default=None, max_length=120),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = await ArtifactService(db).list(_workspace_scope(auth), limit=limit, run_id=run_id)
    return {"artifacts": [artifact_json(row) for row in rows]}


@router.get("/artifacts/{artifact_id}/download")
async def download_workspace_artifact(
    artifact_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    service = ArtifactService(db)
    scope = _workspace_scope(auth)
    row = await service.get(scope, artifact_id)
    raw = await service.read_bytes(scope, artifact_id)
    encoded = quote(row.filename, safe="")
    return Response(
        content=raw,
        media_type=row.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/personal/artifacts/upload")
async def upload_personal_artifacts(
    files: list[UploadFile] = File(default=[]),
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = await _save_uploads(
        files=files,
        service=ArtifactService(db),
        scope=_personal_scope(auth),
        actor_id=auth.user.id,
        source="personal_upload",
    )
    await db.commit()
    return {"artifacts": rows, "artifact_ids": [row["artifact_id"] for row in rows]}


@router.get("/personal/artifacts")
async def list_personal_artifacts(
    limit: int = Query(default=100, ge=1, le=200),
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = await ArtifactService(db).list(_personal_scope(auth), limit=limit)
    return {"artifacts": [artifact_json(row) for row in rows]}


@router.get("/personal/artifacts/{artifact_id}/download")
async def download_personal_artifact(
    artifact_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    service = ArtifactService(db)
    scope = _personal_scope(auth)
    row = await service.get(scope, artifact_id)
    raw = await service.read_bytes(scope, artifact_id)
    encoded = quote(row.filename, safe="")
    return Response(
        content=raw,
        media_type=row.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/artifacts/ui", response_class=HTMLResponse)
async def artifact_ingress_ui(auth: AuthContext = Depends(get_auth_context)):
    """Small authenticated large-N ingress UI; uploads are chunked client-side."""
    workspace = html.escape(auth.tenant.name)
    # Keep this intentionally dependency-free so it works even if the SPA bundle is
    # unavailable during runtime recovery/debugging.
    return HTMLResponse(
        f"""<!doctype html>
<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Operly Artifact Library</title>
<style>
body{{font-family:Inter,ui-sans-serif,system-ui;background:#0f0c18;color:#f4f0ff;margin:0;padding:32px}}
main{{max-width:900px;margin:auto}} .card{{background:#181126;border:1px solid #38284f;border-radius:18px;padding:22px;margin:18px 0}}
button{{background:#7c5cff;color:white;border:0;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer}}
input{{display:block;margin:12px 0}} pre{{white-space:pre-wrap;word-break:break-word;background:#0c0912;padding:14px;border-radius:10px}}
a{{color:#b9a7ff}}
</style></head><body><main>
<h1>Artifact Library</h1><p>Workspace: <strong>{workspace}</strong>. Select hundreds of files; Operly uploads them in bounded 20-file chunks and returns durable artifact IDs.</p>
<div class='card'><input id='files' type='file' multiple><button id='upload'>Upload selected files</button><p id='status'>No upload running.</p><pre id='ids'>[]</pre></div>
<div class='card'><button id='refresh'>Refresh recent artifacts</button><div id='recent'></div></div>
<script>
function csrf(){{const m=document.cookie.match(/(?:^|; )__Host-operly_csrf=([^;]+)/)||document.cookie.match(/(?:^|; )operly_csrf=([^;]+)/);return m?decodeURIComponent(m[1]):''}}
async function api(url,opts={{}}){{opts.credentials='same-origin';opts.headers=Object.assign({{}},opts.headers||{{}},opts.method&&opts.method!=='GET'?{{'X-CSRF-Token':csrf()}}:{{}});const r=await fetch(url,opts);if(!r.ok)throw new Error(await r.text());return r.json()}}
async function refresh(){{const data=await api('/api/artifacts?limit=100');document.getElementById('recent').innerHTML=data.artifacts.map(a=>`<p><a href="/api/artifacts/${{a.artifact_id}}/download">${{a.filename}}</a> · ${{Math.ceil(a.size_bytes/1024)}} KB · <code>${{a.artifact_id}}</code></p>`).join('')||'<p>No artifacts yet.</p>'}}
document.getElementById('refresh').onclick=refresh;
document.getElementById('upload').onclick=async()=>{{const files=[...document.getElementById('files').files];const ids=[];for(let i=0;i<files.length;i+=20){{const chunk=files.slice(i,i+20),form=new FormData();chunk.forEach(f=>form.append('files',f));document.getElementById('status').textContent=`Uploading ${{i+1}}–${{Math.min(i+chunk.length,files.length)}} of ${{files.length}}…`;const data=await api('/api/artifacts/upload',{{method:'POST',body:form}});ids.push(...data.artifact_ids);document.getElementById('ids').textContent=JSON.stringify(ids,null,2)}}document.getElementById('status').textContent=`Uploaded ${{ids.length}} files.`;await refresh()}};
refresh();
</script></main></body></html>""",
        headers={"Cache-Control": "no-store"},
    )
