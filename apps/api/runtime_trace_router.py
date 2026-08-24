"""Authenticated AI runtime-trace reports and run browser endpoints."""
from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.database.agent_models import AgentConversation
from packages.database.model_trace import _trace_json, conversation_trace_report
from packages.database.model_trace_models import ModelRuntimeTrace
from packages.database.models import TenantMember
from packages.database.principal_models import Principal, PrincipalConversation
from packages.database.studio_source_models import StudioAgentRun, StudioModelTrace
from packages.studio.model_trace import trace_json as studio_trace_json

router = APIRouter(prefix="/api/runtime-traces", tags=["runtime-traces"])


def _decoded_payload(payload_json: str | None) -> dict[str, Any]:
    try:
        value = json.loads(payload_json or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _usage_values(value: Any) -> tuple[int, int, int]:
    if not isinstance(value, dict):
        return (0, 0, 0)
    input_tokens = int(value.get("input_tokens") or value.get("prompt_tokens") or 0)
    output_tokens = int(value.get("output_tokens") or value.get("completion_tokens") or 0)
    total_tokens = int(value.get("total_tokens") or input_tokens + output_tokens or 0)
    return input_tokens, output_tokens, total_tokens


def _runtime_run_summary(run_id: str, rows: list[ModelRuntimeTrace]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: row.created_at)
    errors = [row for row in ordered if row.phase == "error"]
    successes = [row for row in ordered if row.phase == "success"]
    models: list[dict[str, str]] = []
    seen_models: set[tuple[str, str]] = set()
    components: list[str] = []
    seen_components: set[str] = set()
    input_tokens = output_tokens = total_tokens = 0

    for row in ordered:
        model_key = (row.provider, row.provider_model_id)
        if model_key not in seen_models:
            seen_models.add(model_key)
            models.append({"provider": row.provider, "model": row.provider_model_id})
        if row.component and row.component not in seen_components:
            seen_components.add(row.component)
            components.append(row.component)
        if row.phase == "success":
            envelope = _decoded_payload(row.payload_json)
            payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
            output = payload.get("output") if isinstance(payload, dict) else {}
            usage = output.get("usage") if isinstance(output, dict) else None
            in_count, out_count, total_count = _usage_values(usage)
            input_tokens += in_count
            output_tokens += out_count
            total_tokens += total_count

    if errors and not successes:
        status = "failed"
    elif errors and successes:
        status = "recovered"
    elif successes:
        status = "success"
    else:
        status = "running"

    first = ordered[0]
    last = ordered[-1]
    return {
        "kind": "runtime",
        "runId": run_id,
        "conversationId": first.conversation_id,
        "tenantId": first.tenant_id,
        "userId": first.user_id,
        "surface": first.surface or first.channel or "runtime",
        "channel": first.channel,
        "components": components,
        "status": status,
        "startedAt": first.created_at.isoformat(),
        "finishedAt": last.created_at.isoformat(),
        "entryCount": len(ordered),
        "errorCount": len(errors),
        "successCount": len(successes),
        "modelCandidatesObserved": models,
        "tokenUsage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": total_tokens,
        },
    }


def _studio_run_summary(run: StudioAgentRun, rows: list[StudioModelTrace]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (row.call_index, row.created_at))
    models: list[dict[str, str]] = []
    seen_models: set[tuple[str, str]] = set()
    input_tokens = output_tokens = total_tokens = 0
    error_count = 0
    success_count = 0

    for row in ordered:
        envelope = _decoded_payload(row.payload_json)
        payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
        if row.phase == "request" and isinstance(payload, dict):
            for candidate in payload.get("candidateModels") or []:
                if not isinstance(candidate, dict):
                    continue
                provider = str(candidate.get("provider") or "unknown")
                model = str(candidate.get("providerModelId") or candidate.get("resourceId") or "unknown")
                key = (provider, model)
                if key not in seen_models:
                    seen_models.add(key)
                    models.append({"provider": provider, "model": model})
        elif row.phase == "response" and isinstance(payload, dict):
            success_count += 1
            provider = str(payload.get("provider") or "unknown")
            model = str(payload.get("providerModelId") or payload.get("modelResourceId") or "unknown")
            key = (provider, model)
            if key not in seen_models:
                seen_models.add(key)
                models.append({"provider": provider, "model": model})
            in_count, out_count, total_count = _usage_values(payload.get("usage"))
            input_tokens += in_count
            output_tokens += out_count
            total_tokens += total_count
        elif row.phase == "error":
            error_count += 1

    status = run.state
    if status not in {"queued", "running", "succeeded", "failed", "cancelled"}:
        status = "success" if success_count else "failed" if error_count else status
    if status == "succeeded":
        status = "success"

    return {
        "kind": "studio",
        "runId": run.id,
        "conversationId": None,
        "tenantId": run.tenant_id,
        "userId": run.created_by,
        "surface": "studio",
        "channel": "studio",
        "components": ["studio_agent"],
        "status": status,
        "operation": run.operation,
        "projectId": run.project_id,
        "startedAt": (run.started_at or run.created_at).isoformat(),
        "finishedAt": (run.completed_at or run.started_at or run.created_at).isoformat(),
        "entryCount": len(ordered),
        "errorCount": error_count + (1 if run.state == "failed" and not error_count else 0),
        "successCount": success_count,
        "modelCandidatesObserved": models,
        "tokenUsage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": total_tokens,
        },
    }


async def _tenant_owner(db: AsyncSession, *, user_id: str, tenant_id: str) -> TenantMember:
    membership = await db.scalar(
        select(TenantMember).where(
            TenantMember.user_id == user_id,
            TenantMember.tenant_id == tenant_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if membership.role != "owner":
        raise HTTPException(status_code=403, detail="Only workspace owners can inspect AI run traces")
    return membership


async def _runtime_rows_for_runs(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: str | None,
    limit: int,
) -> list[ModelRuntimeTrace]:
    filters = (
        [ModelRuntimeTrace.tenant_id == tenant_id]
        if tenant_id
        else [ModelRuntimeTrace.user_id == user_id, ModelRuntimeTrace.tenant_id.is_(None)]
    )
    run_ids = list(
        (
            await db.scalars(
                select(ModelRuntimeTrace.run_id)
                .where(*filters)
                .group_by(ModelRuntimeTrace.run_id)
                .order_by(desc(func.max(ModelRuntimeTrace.created_at)))
                .limit(limit)
            )
        ).all()
    )
    if not run_ids:
        return []
    return list(
        (
            await db.scalars(
                select(ModelRuntimeTrace)
                .where(*filters, ModelRuntimeTrace.run_id.in_(run_ids))
                .order_by(ModelRuntimeTrace.created_at.asc())
            )
        ).all()
    )


@router.get("/runs")
async def list_ai_runs(
    tenant_id: str | None = Query(default=None),
    limit: int = Query(default=75, ge=1, le=200),
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """List AI executions across the shared runtime and Studio trace stores.

    Workspace mode is owner-only because the entries can contain complete model-visible
    business context. Without ``tenant_id`` this endpoint returns only the signed-in
    person's personal, non-workspace model runs.
    """
    user_id = account.user.id
    if tenant_id:
        await _tenant_owner(db, user_id=user_id, tenant_id=tenant_id)

    runtime_rows = await _runtime_rows_for_runs(
        db,
        user_id=user_id,
        tenant_id=tenant_id,
        limit=limit,
    )
    grouped: OrderedDict[str, list[ModelRuntimeTrace]] = OrderedDict()
    for row in runtime_rows:
        grouped.setdefault(row.run_id, []).append(row)
    runs = [_runtime_run_summary(run_id, rows) for run_id, rows in grouped.items()]

    if tenant_id:
        studio_runs = list(
            (
                await db.scalars(
                    select(StudioAgentRun)
                    .where(StudioAgentRun.tenant_id == tenant_id)
                    .order_by(desc(StudioAgentRun.created_at))
                    .limit(limit)
                )
            ).all()
        )
        studio_rows: list[StudioModelTrace] = []
        if studio_runs:
            studio_rows = list(
                (
                    await db.scalars(
                        select(StudioModelTrace)
                        .where(StudioModelTrace.run_id.in_([run.id for run in studio_runs]))
                        .order_by(StudioModelTrace.created_at.asc())
                    )
                ).all()
            )
        rows_by_run: dict[str, list[StudioModelTrace]] = {}
        for row in studio_rows:
            rows_by_run.setdefault(row.run_id, []).append(row)
        runs.extend(_studio_run_summary(run, rows_by_run.get(run.id, [])) for run in studio_runs)

    runs.sort(key=lambda item: str(item.get("startedAt") or ""), reverse=True)
    runs = runs[:limit]
    return {
        "scope": "workspace" if tenant_id else "personal",
        "tenantId": tenant_id,
        "redactionApplied": True,
        "hiddenReasoningRedacted": True,
        "runCount": len(runs),
        "runs": runs,
    }


@router.get("/runs/{run_id}")
async def get_ai_run(
    run_id: str,
    tenant_id: str | None = Query(default=None),
    kind: str = Query(default="runtime", pattern="^(runtime|studio)$"),
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Return every persisted, model-visible trace packet for one AI execution."""
    user_id = account.user.id
    if tenant_id:
        await _tenant_owner(db, user_id=user_id, tenant_id=tenant_id)

    if kind == "studio":
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Studio traces require a workspace")
        run = await db.get(StudioAgentRun, run_id)
        if run is None or run.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="AI run not found")
        rows = list(
            (
                await db.scalars(
                    select(StudioModelTrace)
                    .where(StudioModelTrace.tenant_id == tenant_id, StudioModelTrace.run_id == run_id)
                    .order_by(StudioModelTrace.call_index.asc(), StudioModelTrace.created_at.asc())
                    .limit(5000)
                )
            ).all()
        )
        return {
            **_studio_run_summary(run, rows),
            "redactionApplied": True,
            "hiddenReasoningRedacted": True,
            "instruction": run.instruction,
            "entries": [studio_trace_json(row) for row in rows],
        }

    filters = [ModelRuntimeTrace.run_id == run_id]
    if tenant_id:
        filters.append(ModelRuntimeTrace.tenant_id == tenant_id)
    else:
        filters.extend(
            [
                ModelRuntimeTrace.user_id == user_id,
                ModelRuntimeTrace.tenant_id.is_(None),
            ]
        )
    rows = list(
        (
            await db.scalars(
                select(ModelRuntimeTrace)
                .where(*filters)
                .order_by(ModelRuntimeTrace.created_at.asc())
                .limit(10000)
            )
        ).all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="AI run not found")
    return {
        **_runtime_run_summary(run_id, rows),
        "redactionApplied": True,
        "hiddenReasoningRedacted": True,
        "entries": [_trace_json(row) for row in rows],
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation_runtime_trace(
    conversation_id: str,
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Return model attempts, inputs, outputs, routing metadata, and failures.

    This endpoint is account-authenticated and verifies conversation ownership before
    returning trace payloads. Workspace traces additionally require a current
    membership, so leaving a workspace revokes access to its historical debug data.
    Provider credentials are never recorded; credential-shaped content is redacted.
    """
    user_id = account.user.id

    workspace_conversation = await db.get(AgentConversation, conversation_id)
    if (
        workspace_conversation is not None
        and workspace_conversation.principal_id in {f"user:{user_id}", f"web-user:{user_id}"}
    ):
        membership = await db.scalar(
            select(TenantMember).where(
                TenantMember.user_id == user_id,
                TenantMember.tenant_id == workspace_conversation.tenant_id,
            )
        )
        if membership is not None:
            report = await conversation_trace_report(
                db,
                conversation_id=workspace_conversation.id,
                user_id=user_id,
                tenant_id=workspace_conversation.tenant_id,
            )
            return {
                "scope": "workspace",
                "tenantId": workspace_conversation.tenant_id,
                **report,
            }

    principal = await db.scalar(
        select(Principal).where(
            Principal.kind == "human",
            Principal.user_id == user_id,
        )
    )
    if principal is not None:
        personal_conversation = await db.scalar(
            select(PrincipalConversation).where(
                PrincipalConversation.principal_id == principal.id,
                PrincipalConversation.provider == "operly_web",
                PrincipalConversation.external_conversation_id == conversation_id,
            )
        )
        if personal_conversation is not None:
            report = await conversation_trace_report(
                db,
                conversation_id=personal_conversation.external_conversation_id,
                user_id=user_id,
            )
            return {
                "scope": "personal",
                "principalConversationId": personal_conversation.id,
                **report,
            }

    raise HTTPException(status_code=404, detail="Conversation not found")
