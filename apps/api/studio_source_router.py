import json
import mimetypes
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.business_brain.ollama_client import OllamaError
from packages.coding_harness.opencode_agent import CodingAgentNeedsUserInput, CodingHarnessError
from packages.custom_software.source_bundles import normalized_path
from packages.database.studio_source_models import StudioAgentRun
from packages.studio.agent_runs import create_run, latest_run as latest_agent_run, run_json
from packages.studio.service import StudioService
from packages.studio.source_agent import (
    edit_source,
    file_map,
    generate_source,
    get_source,
    latest_source,
    rollback_source,
    source_json,
)

router = APIRouter(tags=["studio-source"])
service = StudioService()


class SourceEditInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=20_000)
    context: dict = Field(default_factory=dict)


class SourceGenerateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context: dict = Field(default_factory=dict)


class SourceRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["generate", "edit"]
    instruction: str = Field(default="", max_length=20_000)
    context: dict = Field(default_factory=dict)


def _assert_owner(auth: AuthContext) -> None:
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only owners can change website source")


def _agent_error(error: Exception) -> HTTPException:
    if isinstance(error, CodingAgentNeedsUserInput):
        return HTTPException(
            status_code=409,
            detail={
                "code": "studio_clarification_required",
                "message": error.question,
                "question": error.question,
                "options": error.options,
            },
        )
    if isinstance(error, OllamaError):
        return HTTPException(status_code=503, detail=error.public_message)
    return HTTPException(
        status_code=422,
        detail={
            "code": "studio_source_change_blocked",
            "message": str(error)[:1000] or "The website change could not be completed safely.",
        },
    )


@router.get("/api/studio/projects/{project_id}/source")
async def current_source(
    project_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        await service.project(db, auth.tenant.id, project_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Project not found") from error
    row = await latest_source(db, auth.tenant.id, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="This website is still using its legacy Studio version")
    return source_json(row)


@router.post("/api/studio/projects/{project_id}/source/runs", status_code=202)
async def start_source_run(
    project_id: str,
    payload: SourceRunInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Start a durable source-agent run and return immediately.

    Studio polls the run to display the authorized model, attached context, model
    turns, tool actions, validation failures, retries and completion. The trace is
    intentionally operational and never exposes private chain-of-thought.
    """
    _assert_owner(auth)
    if payload.operation == "edit" and not payload.instruction.strip():
        raise HTTPException(status_code=422, detail={"code": "invalid_instruction", "message": "Instruction is required"})
    try:
        project = await service.project(db, auth.tenant.id, project_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Project not found") from error
    run, created = await create_run(
        db,
        auth.tenant.id,
        auth.user.id,
        project,
        operation=payload.operation,
        instruction=payload.instruction,
        context=payload.context,
    )
    result = await run_json(db, run)
    result["created"] = created
    return result


@router.get("/api/studio/projects/{project_id}/source/runs/latest")
async def latest_source_run(
    project_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        await service.project(db, auth.tenant.id, project_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Project not found") from error
    run = await latest_agent_run(db, auth.tenant.id, project_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No Studio agent run exists for this website")
    return await run_json(db, run)


@router.get("/api/studio/projects/{project_id}/source/runs/{run_id}")
async def get_source_run(
    project_id: str,
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    run = await db.get(StudioAgentRun, run_id)
    if run is None or run.tenant_id != auth.tenant.id or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Studio agent run not found")
    return await run_json(db, run)


# Compatibility endpoints retained for older clients. The current Studio browser
# uses /source/runs so long model work is durable and observable.
@router.post("/api/studio/projects/{project_id}/source/generate")
async def generate_project_source(
    project_id: str,
    payload: SourceGenerateInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _assert_owner(auth)
    try:
        project = await service.project(db, auth.tenant.id, project_id)
        row = await generate_source(
            db,
            auth.tenant.id,
            auth.user.id,
            project,
            editor_context=payload.context,
        )
        await db.commit()
        await db.refresh(row)
        return source_json(row)
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Project not found") from error
    except (CodingAgentNeedsUserInput, CodingHarnessError, OllamaError) as error:
        await db.rollback()
        raise _agent_error(error) from error


@router.post("/api/studio/projects/{project_id}/source/edits")
async def edit_project_source(
    project_id: str,
    payload: SourceEditInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _assert_owner(auth)
    try:
        project = await service.project(db, auth.tenant.id, project_id)
        row = await edit_source(
            db,
            auth.tenant.id,
            auth.user.id,
            project,
            payload.instruction,
            editor_context=payload.context,
        )
        await db.commit()
        await db.refresh(row)
        return source_json(row)
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Project not found") from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail={"code": "invalid_instruction", "message": str(error)}) from error
    except (CodingAgentNeedsUserInput, CodingHarnessError, OllamaError) as error:
        await db.rollback()
        raise _agent_error(error) from error


@router.post("/api/studio/projects/{project_id}/source/{source_id}/rollback")
async def restore_project_source(
    project_id: str,
    source_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _assert_owner(auth)
    try:
        project = await service.project(db, auth.tenant.id, project_id)
        target = await get_source(db, auth.tenant.id, project_id, source_id)
        row = await rollback_source(db, auth.tenant.id, auth.user.id, project, target)
        await db.commit()
        await db.refresh(row)
        return source_json(row)
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error


BRIDGE = r"""
<script data-operly-studio-bridge>
(() => {
  if (window === window.parent) return;
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const selector = el => {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 5) {
      let part = node.tagName.toLowerCase();
      if (node.classList && node.classList.length) part += '.' + [...node.classList].slice(0,2).map(CSS.escape).join('.');
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(' > ');
  };
  document.addEventListener('click', event => {
    const el = event.target && event.target.closest ? event.target.closest('*') : null;
    if (!el) return;
    event.preventDefault();
    event.stopPropagation();
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const style = {};
    ['display','position','width','height','color','backgroundColor','fontSize','fontWeight','lineHeight','padding','margin','gap','borderRadius','flexDirection','justifyContent','alignItems','gridTemplateColumns'].forEach(key => {
      if (cs[key] && cs[key] !== 'none' && cs[key] !== 'normal') style[key] = clean(cs[key]).slice(0,200);
    });
    document.querySelectorAll('[data-operly-studio-selected]').forEach(node => {
      node.style.outline = node.dataset.operlyPriorOutline || '';
      node.removeAttribute('data-operly-studio-selected');
    });
    el.dataset.operlyPriorOutline = el.style.outline || '';
    el.dataset.operlyStudioSelected = '1';
    el.style.outline = '2px solid #65b7ff';
    el.style.outlineOffset = '3px';
    parent.postMessage({
      type: 'OPERLY_STUDIO_SELECT',
      selection: {
        selector: selector(el),
        tag: el.tagName.toLowerCase(),
        text: clean(el.textContent).slice(0,900),
        outerHTML: clean(el.outerHTML).slice(0,2600),
        rect: {x:Math.round(rect.x), y:Math.round(rect.y), width:Math.round(rect.width), height:Math.round(rect.height)},
        computedStyle: style,
        page: {title: document.title, path: location.pathname}
      }
    }, '*');
  }, true);
})();
</script>
"""


def _inject_bridge(html: str) -> str:
    if "data-operly-studio-bridge" in html:
        return html
    match = html.lower().rfind("</body>")
    if match >= 0:
        return html[:match] + BRIDGE + html[match:]
    return html + BRIDGE


@router.get("/api/studio/projects/{project_id}/source/preview")
@router.get("/api/studio/projects/{project_id}/source/preview/")
@router.get("/api/studio/projects/{project_id}/source/preview/{path:path}")
async def preview_project_source(
    project_id: str,
    request: Request,
    path: str = "index.html",
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        await service.project(db, auth.tenant.id, project_id)
        source_id = request.query_params.get("sourceId")
        row = await get_source(db, auth.tenant.id, project_id, source_id) if source_id else await latest_source(db, auth.tenant.id, project_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Preview not found") from error
    if row is None:
        raise HTTPException(status_code=404, detail="Source preview not found")

    requested = path or "index.html"
    try:
        requested = normalized_path(requested)
    except Exception as error:
        raise HTTPException(status_code=404, detail="Preview file not found") from error
    records = file_map(row)
    if requested not in records:
        raise HTTPException(status_code=404, detail="Preview file not found")
    content = records[requested]
    media_type = mimetypes.guess_type(requested)[0] or "application/octet-stream"
    if media_type == "text/html" and "studio" in request.query_params:
        content = _inject_bridge(content)

    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if media_type == "text/html":
        headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; font-src 'self' data: https:; connect-src 'none'; "
            "frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        )
    return Response(content, media_type=media_type, headers=headers)
