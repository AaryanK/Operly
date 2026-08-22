"""Durable Studio source-agent runs with owner-visible operational progress.

The trace intentionally exposes context/tool/validation progress, not private model
chain-of-thought. Runs survive browser refreshes and the browser never has to hold
one long model request open just to know whether work is progressing.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select

from packages.business_brain.ollama_client import OllamaError
from packages.coding_harness.opencode_agent import CodingAgentNeedsUserInput, CodingHarnessError
from packages.database.db import SessionFactory
from packages.database.studio_models import StudioProject
from packages.database.studio_source_models import StudioAgentEvent, StudioAgentRun
from packages.model_runtime.portfolio import model_route
from packages.studio.source_agent import edit_source, generate_source, latest_source, source_json

ACTIVE_STATES = {"queued", "running"}
TERMINAL_STATES = {"succeeded", "failed", "needs_input"}
_TASKS: set[asyncio.Task] = set()


def _clip(value: Any, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _safe_detail(value: dict[str, Any] | None) -> dict[str, Any]:
    detail = value or {}
    clean: dict[str, Any] = {}
    for key, item in detail.items():
        if item is None:
            continue
        if isinstance(item, (bool, int, float)):
            clean[str(key)] = item
        elif isinstance(item, str):
            clean[str(key)] = item[:4000]
        elif isinstance(item, (list, dict)):
            try:
                clean[str(key)] = json.loads(json.dumps(item, ensure_ascii=False, default=str)[:8000])
            except Exception:
                clean[str(key)] = _clip(item, 2000)
        else:
            clean[str(key)] = _clip(item, 2000)
    return clean


async def record_event(
    run_id: str,
    tenant_id: str,
    phase: str,
    summary: str,
    *,
    detail: dict[str, Any] | None = None,
) -> None:
    """Persist one sanitized event independently from the long-running source transaction."""
    async with SessionFactory() as db:
        run = await db.get(StudioAgentRun, run_id)
        if run is None or run.tenant_id != tenant_id:
            return
        run.event_count = int(run.event_count or 0) + 1
        db.add(
            StudioAgentEvent(
                tenant_id=tenant_id,
                run_id=run.id,
                sequence=run.event_count,
                phase=_clip(phase, 40) or "progress",
                summary=_clip(summary, 1000) or "Studio agent progress",
                detail_json=json.dumps(_safe_detail(detail), ensure_ascii=False, default=str),
            )
        )
        await db.commit()


def _context_summary(context: dict[str, Any], source) -> tuple[str, dict[str, Any]]:
    selection = context.get("selection") or context.get("selected") or None
    conversation = context.get("conversation") if isinstance(context.get("conversation"), list) else []
    selected_label = "whole page"
    selected_detail = None
    if isinstance(selection, dict):
        selected_label = _clip(selection.get("componentType") or selection.get("tag") or selection.get("selector") or "selected element", 180)
        selected_detail = {
            "tag": _clip(selection.get("tag"), 80),
            "selector": _clip(selection.get("selector"), 500),
            "text": _clip(selection.get("text"), 500),
        }
    detail = {
        "route": _clip(context.get("route") or context.get("page") or "/", 300),
        "viewport": _clip(context.get("viewport") or "desktop", 80),
        "selection": selected_detail,
        "conversationTurns": len(conversation[-10:]),
        "businessContext": "attached",
        "source": f"S{source.source_version}" if source is not None else "legacy/new website",
    }
    summary = (
        f"Context attached: business profile · {detail['source']} · {selected_label} · "
        f"{detail['viewport']} · {detail['conversationTurns']} recent Studio turn"
        f"{'s' if detail['conversationTurns'] != 1 else ''}."
    )
    return summary, detail


async def create_run(
    db,
    tenant_id: str,
    user_id: str,
    project: StudioProject,
    *,
    operation: str,
    instruction: str = "",
    context: dict[str, Any] | None = None,
) -> tuple[StudioAgentRun, bool]:
    """Create one run, or return the already-active run for this project."""
    active = await db.scalar(
        select(StudioAgentRun)
        .where(
            StudioAgentRun.tenant_id == tenant_id,
            StudioAgentRun.project_id == project.id,
            StudioAgentRun.state.in_(sorted(ACTIVE_STATES)),
        )
        .order_by(StudioAgentRun.created_at.desc())
    )
    if active is not None:
        return active, False

    run = StudioAgentRun(
        tenant_id=tenant_id,
        project_id=project.id,
        operation=operation,
        instruction=_clip(instruction, 20_000),
        context_json=json.dumps(context or {}, ensure_ascii=False, default=str),
        state="queued",
        created_by=user_id,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    await record_event(run.id, tenant_id, "queued", "Studio request queued. Preparing the authorized model and project context.")
    launch_run(run.id)
    return run, True


def launch_run(run_id: str) -> None:
    task = asyncio.create_task(_execute_run(run_id))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _mark_failed(run_id: str, state: str, message: str) -> None:
    async with SessionFactory() as db:
        run = await db.get(StudioAgentRun, run_id)
        if run is None:
            return
        run.state = state
        run.error_message = _clip(message, 4000)
        run.completed_at = datetime.utcnow()
        await db.commit()
    await record_event(
        run_id,
        run.tenant_id,
        "needs_input" if state == "needs_input" else "error",
        _clip(message, 1000) or "The source change stopped safely.",
        detail={"state": state},
    )


async def _execute_run(run_id: str) -> None:
    async with SessionFactory() as db:
        run = await db.get(StudioAgentRun, run_id)
        if run is None:
            return
        project = await db.scalar(
            select(StudioProject).where(
                StudioProject.id == run.project_id,
                StudioProject.tenant_id == run.tenant_id,
            )
        )
        if project is None:
            await db.close()
            await _mark_failed(run_id, "failed", "Studio project no longer exists.")
            return

        route = model_route("coding")
        run.state = "running"
        run.model_id = route.primary
        run.started_at = datetime.utcnow()
        await db.commit()
        await record_event(
            run.id,
            run.tenant_id,
            "start",
            f"Starting source agent with authorized model {route.primary}.",
            detail={"model": route.primary, "operation": run.operation},
        )
        source = await latest_source(db, run.tenant_id, project.id)
        try:
            context = json.loads(run.context_json or "{}")
            if not isinstance(context, dict):
                context = {}
        except Exception:
            context = {}
        summary, context_detail = _context_summary(context, source)
        await record_event(run.id, run.tenant_id, "context", summary, detail=context_detail)

        async def progress(event: dict[str, Any]) -> None:
            phase = _clip(event.get("phase") or "progress", 40)
            summary_text = _clip(event.get("summary") or "Studio agent progress", 1000)
            detail = {
                "step": event.get("step"),
                "tool": event.get("tool"),
                "path": event.get("path"),
                "ok": event.get("ok"),
                "detail": event.get("detail"),
                "elapsedSeconds": event.get("elapsedSeconds"),
                "remainingSeconds": event.get("remainingSeconds"),
            }
            await record_event(run.id, run.tenant_id, phase, summary_text, detail=detail)

        try:
            if run.operation == "generate":
                row = await generate_source(
                    db,
                    run.tenant_id,
                    run.created_by,
                    project,
                    editor_context=context,
                    progress_callback=progress,
                )
            elif run.operation == "edit":
                row = await edit_source(
                    db,
                    run.tenant_id,
                    run.created_by,
                    project,
                    run.instruction,
                    editor_context=context,
                    progress_callback=progress,
                )
            else:
                raise CodingHarnessError(f"Unsupported Studio run operation: {run.operation}")

            payload = source_json(row)
            run.source_id = row.id
            run.model_id = payload.get("modelId") or run.model_id
            run.state = "succeeded"
            run.error_message = None
            run.completed_at = datetime.utcnow()
            await db.commit()
            await record_event(
                run.id,
                run.tenant_id,
                "complete",
                payload.get("summary") or f"Source S{payload.get('sourceVersion')} is ready.",
                detail={
                    "sourceId": row.id,
                    "sourceVersion": payload.get("sourceVersion"),
                    "model": run.model_id,
                    "changedPaths": payload.get("changedPaths") or [],
                },
            )
        except CodingAgentNeedsUserInput as error:
            await db.rollback()
            await _mark_failed(run_id, "needs_input", error.question)
        except OllamaError as error:
            await db.rollback()
            await _mark_failed(run_id, "failed", error.public_message)
        except Exception as error:
            await db.rollback()
            await _mark_failed(run_id, "failed", str(error)[:4000] or "Studio source change failed safely.")


async def events_for_run(db, tenant_id: str, run_id: str) -> list[StudioAgentEvent]:
    return list(
        (
            await db.scalars(
                select(StudioAgentEvent)
                .where(StudioAgentEvent.tenant_id == tenant_id, StudioAgentEvent.run_id == run_id)
                .order_by(StudioAgentEvent.sequence.asc())
                .limit(200)
            )
        ).all()
    )


def event_json(event: StudioAgentEvent) -> dict[str, Any]:
    try:
        detail = json.loads(event.detail_json or "{}")
    except Exception:
        detail = {}
    return {
        "sequence": event.sequence,
        "phase": event.phase,
        "summary": event.summary,
        "detail": detail,
        "createdAt": event.created_at.isoformat(),
    }


async def run_json(db, run: StudioAgentRun) -> dict[str, Any]:
    events = await events_for_run(db, run.tenant_id, run.id)
    elapsed = None
    if run.started_at:
        end = run.completed_at or datetime.utcnow()
        elapsed = max(0, round((end - run.started_at).total_seconds(), 1))
    return {
        "id": run.id,
        "projectId": run.project_id,
        "operation": run.operation,
        "instruction": run.instruction,
        "state": run.state,
        "modelId": run.model_id,
        "sourceId": run.source_id,
        "error": run.error_message,
        "eventCount": run.event_count,
        "elapsedSeconds": elapsed,
        "createdAt": run.created_at.isoformat(),
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "completedAt": run.completed_at.isoformat() if run.completed_at else None,
        "events": [event_json(item) for item in events],
    }


async def latest_run(db, tenant_id: str, project_id: str) -> StudioAgentRun | None:
    return await db.scalar(
        select(StudioAgentRun)
        .where(StudioAgentRun.tenant_id == tenant_id, StudioAgentRun.project_id == project_id)
        .order_by(StudioAgentRun.created_at.desc())
    )
