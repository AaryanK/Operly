from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any, Iterable
from urllib.parse import quote

from sqlalchemy import select

from packages.artifacts.service import ArtifactScope, ArtifactService, artifact_json
from packages.database.artifact_models import AgentRunEventRecord, AgentRunRecord


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def artifact_ids_from_run(run: dict[str, Any] | None) -> list[str]:
    """Return durable artifact handles emitted by one agent run."""
    value = run if isinstance(run, dict) else {}
    state = value.get("run_state") if isinstance(value.get("run_state"), dict) else {}
    refs = state.get("artifact_refs") or value.get("artifact_ids") or []
    if isinstance(refs, str):
        refs = [refs]
    if not isinstance(refs, (list, tuple, set)):
        return []
    return _dedupe(str(item) for item in refs)


def _scope_run_clauses(scope: ArtifactScope) -> list[Any]:
    clauses: list[Any] = [
        AgentRunRecord.scope_kind == scope.kind,
        AgentRunRecord.scope_id == scope.scope_id,
    ]
    if scope.kind == "workspace":
        clauses.extend(
            [
                AgentRunRecord.tenant_id == scope.tenant_id,
                AgentRunRecord.owner_user_id.is_(None),
            ]
        )
    else:
        clauses.extend(
            [
                AgentRunRecord.owner_user_id == scope.owner_user_id,
                AgentRunRecord.tenant_id.is_(None),
            ]
        )
    return clauses


def _download_url(scope: ArtifactScope, artifact_id: str) -> str:
    prefix = "/api/artifacts" if scope.kind == "workspace" else "/api/personal/artifacts"
    path = f"{prefix}/{quote(str(artifact_id), safe='')}/download"
    base = str(os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    return f"{base}{path}" if base else path


async def resolve_delivery_artifacts(
    db,
    scope: ArtifactScope,
    artifact_ids: Iterable[str],
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Resolve artifact IDs through the scope-enforced store for a surface response.

    Missing/expired handles are omitted rather than leaking cross-scope existence.
    A surface only receives metadata for artifacts it can actually fetch in the same
    execution scope.
    """
    ids = _dedupe(artifact_ids)[: max(0, min(int(limit), 200))]
    if not ids:
        return []
    service = ArtifactService(db)
    output: list[dict[str, Any]] = []
    for artifact_id in ids:
        try:
            row = await service.get(scope, artifact_id)
        except LookupError:
            continue
        item = artifact_json(row)
        item["download_url"] = _download_url(scope, row.id)
        output.append(item)
    return output


async def delivery_artifacts_for_run(
    db,
    scope: ArtifactScope,
    run: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    return await resolve_delivery_artifacts(db, scope, artifact_ids_from_run(run))


def _run_refs(row: AgentRunRecord) -> list[str]:
    try:
        values = json.loads(row.artifact_refs_json or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    return _dedupe(str(item) for item in values)


async def _has_capability_observation(db, run_id: str) -> bool:
    """Return whether the durable run observed any real capability execution.

    This is deliberately evidence-based rather than intent/keyword based. A model may
    answer informational questions without tools, but a generic execution claim such
    as ``Done.`` is not treated as proof that an operation happened when the run has
    no capability observation at all.
    """
    event_id = await db.scalar(
        select(AgentRunEventRecord.id)
        .where(
            AgentRunEventRecord.run_id == run_id,
            AgentRunEventRecord.event_type == "capability.observed",
        )
        .limit(1)
    )
    return event_id is not None


async def project_agent_result(
    db,
    scope: ArtifactScope,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Add verified artifact delivery metadata to an agent result.

    Agent services remain transport-neutral. Web, Discord, MCP and other adapters can
    all call this one projection before rendering their surface-specific response.
    Only persisted artifacts in the selected execution scope are exposed.
    """
    projected = dict(result)
    runtime_run_id = str(projected.get("runtime_run_id") or "").strip()
    run_row = None
    refs = artifact_ids_from_run(projected)
    if runtime_run_id:
        run_row = await db.scalar(
            select(AgentRunRecord).where(
                AgentRunRecord.id == runtime_run_id,
                *_scope_run_clauses(scope),
            )
        )
        if run_row is not None:
            refs = _dedupe([*refs, *_run_refs(run_row)])

    artifacts = await resolve_delivery_artifacts(db, scope, refs)
    projected["artifacts"] = artifacts
    projected["artifact_ids"] = [item["artifact_id"] for item in artifacts]
    projected["delivery"] = {
        "transport": "artifact_refs_v1",
        "runtime_run_id": runtime_run_id or None,
        "artifact_count": len(artifacts),
        "run_state": str(getattr(run_row, "state", "") or "") or None,
    }

    # Never let a generic fallback hide verified output, a failed durable run, or a
    # complete absence of execution evidence. Specific informational prose is left
    # alone; only empty/generic completion claims are hardened here.
    message = str(projected.get("message") or "").strip()
    generic = message.lower().rstrip(".! ") in {"", "done", "completed"}
    if artifacts and generic:
        names = ", ".join(f"`{item['filename']}`" for item in artifacts[:3])
        suffix = "" if len(artifacts) <= 3 else f" and {len(artifacts) - 3} more"
        projected["message"] = f"Created {names}{suffix}."
    elif run_row is not None and str(run_row.state or "").lower() == "failed" and generic:
        projected["message"] = "I couldn't verify completion of that run."
    elif run_row is not None and generic and not await _has_capability_observation(db, run_row.id):
        projected["message"] = "I don't have verified execution evidence that the requested operation completed."
    elif not message:
        projected["message"] = "Done."
    return projected


async def artifacts_by_assistant_message(
    db,
    scope: ArtifactScope,
    *,
    conversation_id: str,
    messages: Iterable[Any],
) -> dict[str, list[dict[str, Any]]]:
    """Project durable run artifacts back onto conversation assistant turns.

    AgentRunRecord already carries conversation_id, timestamps and artifact refs, so
    chat history can become artifact-aware without duplicating binary/message storage
    or adding a message schema migration. Each run is associated with the first
    assistant message written at/after that run started.
    """
    assistant_messages = sorted(
        [
            item
            for item in messages
            if str(getattr(item, "role", "") or "") == "assistant"
            and getattr(item, "id", None)
            and getattr(item, "created_at", None) is not None
        ],
        key=lambda item: item.created_at,
    )
    if not assistant_messages:
        return {}

    runs = list(
        (
            await db.scalars(
                select(AgentRunRecord)
                .where(
                    *_scope_run_clauses(scope),
                    AgentRunRecord.conversation_id == str(conversation_id),
                )
                .order_by(AgentRunRecord.started_at)
            )
        ).all()
    )
    if not runs:
        return {}

    refs_by_message: dict[str, list[str]] = defaultdict(list)
    for run in runs:
        refs = _run_refs(run)
        if not refs:
            continue
        target = next(
            (
                message
                for message in assistant_messages
                if message.created_at >= run.started_at
            ),
            assistant_messages[-1],
        )
        refs_by_message[str(target.id)].extend(refs)

    output: dict[str, list[dict[str, Any]]] = {}
    for message_id, refs in refs_by_message.items():
        resolved = await resolve_delivery_artifacts(db, scope, refs)
        if resolved:
            output[message_id] = resolved
    return output
