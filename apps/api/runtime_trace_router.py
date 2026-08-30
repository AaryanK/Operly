"""Authenticated canonical AgentRuntime trace reports and run browser endpoints."""
from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.database.agent_models import AgentConversation
from packages.database.model_trace import _trace_json, conversation_trace_report
from packages.database.model_trace_models import ModelRuntimeTrace
from packages.database.models import TenantMember
from packages.database.principal_models import Principal, PrincipalConversation
from packages.security.surfaces import SurfaceKind

router = APIRouter(prefix="/api/runtime-traces", tags=["runtime-traces"])

_PERSONAL_TRACE_SURFACES = frozenset(
    {
        "private/direct",
        SurfaceKind.PERSONAL_PRIVATE.value,
        SurfaceKind.DISCORD_DM.value,
        SurfaceKind.WORKSPACE_PRIVATE.value,
    }
)
_WORKSPACE_TRACE_SURFACES = frozenset(
    {
        "shared/workspace",
        SurfaceKind.WORKSPACE_SHARED.value,
        SurfaceKind.DISCORD_GUILD.value,
        SurfaceKind.SYSTEM_TASK.value,
        "solution_generation",
        "studio",
    }
)


def _decoded_payload(payload_json: str | None) -> dict[str, Any]:
    try:
        value = json.loads(payload_json or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _event_payload(row: ModelRuntimeTrace) -> dict[str, Any]:
    """Return the application payload for one persisted runtime event.

    `emit_runtime_trace_event` persists a trace envelope whose payload is an event
    packet (`eventType`, `metadata`, `payload`). Model trace rows use the first payload
    level directly. AI Debug status classification needs the inner application payload
    for runtime events while remaining compatible with older/direct test rows.
    """

    envelope = _decoded_payload(row.payload_json)
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("payload")
    if "eventType" in payload and isinstance(nested, dict):
        return nested
    return payload


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
    terminal_blocks = [
        row
        for row in ordered
        if row.phase == "capability.rejected" and _event_payload(row).get("state") == "blocked"
    ]
    terminal_completions = [
        row
        for row in ordered
        if row.phase == "workflow.completed" and _event_payload(row).get("state") == "completed"
    ]
    models: list[dict[str, str]] = []
    seen_models: set[tuple[str, str]] = set()
    components: list[str] = []
    seen_components: set[str] = set()
    input_tokens = output_tokens = total_tokens = 0
    reported_runtime_tokens = 0

    for row in ordered:
        model_key = (row.provider, row.provider_model_id)
        if model_key != ("operly", "runtime") and model_key not in seen_models:
            seen_models.add(model_key)
            models.append({"provider": row.provider, "model": row.provider_model_id})
        if row.component and row.component not in seen_components:
            seen_components.add(row.component)
            components.append(row.component)

        event_payload = _event_payload(row)
        event_token_usage = event_payload.get("token_usage")
        if isinstance(event_token_usage, (int, float)):
            reported_runtime_tokens = max(reported_runtime_tokens, int(event_token_usage))

        if row.phase == "success":
            envelope = _decoded_payload(row.payload_json)
            payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
            output = payload.get("output") if isinstance(payload, dict) else {}
            usage = output.get("usage") if isinstance(output, dict) else None
            in_count, out_count, total_count = _usage_values(usage)
            input_tokens += in_count
            output_tokens += out_count
            total_tokens += total_count

    if total_tokens == 0 and reported_runtime_tokens:
        total_tokens = reported_runtime_tokens

    if terminal_blocks:
        status = "blocked"
    elif errors and not successes and not terminal_completions:
        status = "failed"
    elif errors and (successes or terminal_completions):
        status = "recovered"
    elif successes or terminal_completions:
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


def _runtime_visibility_filters(*, user_id: str, tenant_id: str | None):
    if tenant_id:
        return [
            ModelRuntimeTrace.tenant_id == tenant_id,
            ModelRuntimeTrace.surface.in_(_WORKSPACE_TRACE_SURFACES),
        ]
    return [
        ModelRuntimeTrace.user_id == user_id,
        ModelRuntimeTrace.surface.in_(_PERSONAL_TRACE_SURFACES),
    ]


async def _runtime_rows_for_runs(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: str | None,
    limit: int,
) -> list[ModelRuntimeTrace]:
    filters = _runtime_visibility_filters(user_id=user_id, tenant_id=tenant_id)
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
    """List canonical AgentRuntime executions visible to the authenticated account."""
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
    runs.sort(key=lambda item: str(item.get("startedAt") or ""), reverse=True)
    return {
        "scope": "workspace" if tenant_id else "personal",
        "tenantId": tenant_id,
        "redactionApplied": True,
        "hiddenReasoningRedacted": True,
        "runCount": len(runs),
        "runs": runs[:limit],
    }


@router.get("/runs/{run_id}")
async def get_ai_run(
    run_id: str,
    tenant_id: str | None = Query(default=None),
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Return every persisted model-visible trace packet for one AgentRuntime execution."""
    user_id = account.user.id
    if tenant_id:
        await _tenant_owner(db, user_id=user_id, tenant_id=tenant_id)

    filters = [ModelRuntimeTrace.run_id == run_id, *_runtime_visibility_filters(user_id=user_id, tenant_id=tenant_id)]
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
    """Return model attempts, inputs, outputs, routing metadata, and failures."""
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
        if membership is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return await conversation_trace_report(
            db,
            conversation_id=workspace_conversation.id,
            user_id=user_id,
            tenant_id=workspace_conversation.tenant_id,
        )

    principal = await db.scalar(select(Principal).where(Principal.user_id == user_id))
    if principal is not None:
        personal_conversation = await db.scalar(
            select(PrincipalConversation).where(
                PrincipalConversation.principal_id == principal.id,
                or_(
                    PrincipalConversation.id == conversation_id,
                    PrincipalConversation.external_conversation_id == conversation_id,
                ),
            )
        )
        if personal_conversation is not None:
            return await conversation_trace_report(
                db,
                conversation_id=personal_conversation.external_conversation_id,
                user_id=user_id,
            )

    raise HTTPException(status_code=404, detail="Conversation not found")
