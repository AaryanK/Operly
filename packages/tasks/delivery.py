from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol

from sqlalchemy import select

from packages.company.events import append_event
from packages.database.agent_models import AgentConversation, AgentMessage
from packages.database.channel_models import ExternalIdentity
from packages.database.db import session_scope
from packages.database.models import AppUser
from packages.database.principal_models import Principal, PrincipalConversation, PrincipalMessage
from packages.model_runtime.trace_events import RuntimeTraceEvent
from packages.security.execution_context import ExecutionContextError, resolve_execution_context


class TaskDeliveryError(RuntimeError):
    pass


class TaskDeliveryAdapter(Protocol):
    providers: tuple[str, ...]

    async def deliver(self, target: dict[str, Any], message: str) -> dict[str, Any]: ...


async def capture_task_origin(context) -> dict[str, Any]:
    invocation = context.invocation or {}
    metadata = invocation.get("metadata") if isinstance(invocation.get("metadata"), dict) else {}
    provider = str(
        metadata.get("origin_provider")
        or invocation.get("channel")
        or metadata.get("origin")
        or "operly"
    ).strip().lower()
    personal_scope = bool(metadata.get("personal_scope"))
    user_id = str(context.actor_id or metadata.get("user_id") or "").strip() or None
    actor_name = str(metadata.get("actor_name") or "").strip()
    if not actor_name and user_id:
        user = await context.db.get(AppUser, user_id)
        if user is not None:
            actor_name = str(user.display_name or "").strip()

    external_user_id = metadata.get("external_user_id") or metadata.get("discord_user_id")
    external_space_id = metadata.get("external_space_id") or metadata.get("discord_guild_id")
    external_conversation_id = (
        metadata.get("external_conversation_id")
        or metadata.get("conversation_id")
        or metadata.get("_conversation_id")
    )
    if provider == "discord":
        raw = str(metadata.get("conversation_id") or "")
        if (external_conversation_id is None or str(external_conversation_id).startswith("discord:")) and raw.startswith("discord:"):
            external_conversation_id = raw.split(":", 1)[1]
        if external_user_id is None and user_id:
            identity = await context.db.scalar(
                select(ExternalIdentity).where(
                    ExternalIdentity.user_id == user_id,
                    ExternalIdentity.provider == "discord",
                )
            )
            if identity is not None:
                external_user_id = identity.provider_subject
                actor_name = actor_name or str(identity.display_name or "").strip()
    elif external_user_id is None:
        external_user_id = user_id

    if external_space_id is None and context.tenant_id and provider != "discord" and not personal_scope:
        external_space_id = context.tenant_id

    return {
        "provider": provider,
        "scope": "personal" if personal_scope else "workspace",
        "tenant_id": context.tenant_id,
        "user_id": user_id,
        "actor_name": actor_name or "Operly user",
        "is_direct": bool(metadata.get("is_direct", personal_scope)),
        "external_user_id": str(external_user_id) if external_user_id is not None else None,
        "external_space_id": str(external_space_id) if external_space_id is not None else None,
        "external_conversation_id": (
            str(external_conversation_id) if external_conversation_id is not None else None
        ),
    }


def delivery_target_from_origin(origin: dict[str, Any], delivery: str = "origin") -> dict[str, Any]:
    provider = str(origin.get("provider") or "operly").strip().lower()
    requested = str(delivery or "origin").strip().lower()
    kind = requested
    if requested == "origin":
        kind = "dm" if bool(origin.get("is_direct")) else "channel"
    return {
        "provider": provider,
        "kind": kind,
        "scope": origin.get("scope"),
        "tenant_id": origin.get("tenant_id"),
        "user_id": origin.get("user_id"),
        "external_user_id": origin.get("external_user_id"),
        "external_space_id": origin.get("external_space_id"),
        "external_conversation_id": origin.get("external_conversation_id"),
    }


async def _reauthorize_delivery_target(target: dict[str, Any]) -> None:
    """Re-evaluate delayed delivery authority instead of trusting creation-time state."""
    scope = str(target.get("scope") or "workspace").strip().lower()
    user_id = str(target.get("user_id") or "").strip()
    if not user_id:
        raise TaskDeliveryError("task_delivery_user_missing")

    if scope == "personal":
        async with session_scope() as db:
            user = await db.get(AppUser, user_id)
            if user is None or not user.active:
                raise TaskDeliveryError("task_delivery_personal_authority_revoked")
        return

    tenant_id = str(target.get("tenant_id") or "").strip()
    if not tenant_id:
        raise TaskDeliveryError("task_delivery_workspace_missing")
    try:
        async with session_scope() as db:
            await resolve_execution_context(
                db,
                workspace_id=tenant_id,
                user_id=user_id,
                channel=f"task_delivery:{str(target.get('provider') or 'unknown')}",
                conversation_id=str(target.get("external_conversation_id") or "") or None,
                metadata={"scheduled_delivery": True},
                require_membership=True,
            )
    except ExecutionContextError as error:
        raise TaskDeliveryError("task_delivery_workspace_authority_revoked") from error


async def _record_workspace_delivery_event(
    target: dict[str, Any],
    event_type: RuntimeTraceEvent,
    payload: dict[str, Any],
) -> None:
    tenant_id = str(target.get("tenant_id") or "").strip()
    if not tenant_id:
        return
    try:
        async with session_scope() as db:
            await append_event(
                db,
                tenant_id=tenant_id,
                event_type=event_type.value,
                payload={
                    "provider": target.get("provider"),
                    "kind": target.get("kind"),
                    "user_id": target.get("user_id"),
                    "external_user_id": target.get("external_user_id"),
                    "external_conversation_id": target.get("external_conversation_id"),
                    **payload,
                },
                source="task_delivery",
            )
    except Exception:
        # Delivery truth must not be changed by telemetry failure. The adapter
        # receipt remains the source of truth for the active run.
        return


def _dedupe_artifact_ids(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output[:20]


async def deliver_task_output(
    target: dict[str, Any],
    message: str,
    *,
    artifact_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Deliver delayed output after a fresh authority check.

    Artifact IDs remain opaque scoped handles. They are passed to the adapter only
    after authority is rechecked; adapters must resolve bytes/URLs through the
    Artifact Store for the exact target scope rather than trusting model-authored
    filenames or links.
    """
    delivery_target = dict(target)
    ids = _dedupe_artifact_ids(artifact_ids)
    if ids:
        delivery_target["artifact_ids"] = ids

    provider = str(delivery_target.get("provider") or "").strip().lower()
    if not provider:
        raise TaskDeliveryError("task_delivery_provider_missing")

    try:
        await _reauthorize_delivery_target(delivery_target)
    except Exception as error:
        await _record_workspace_delivery_event(
            delivery_target,
            RuntimeTraceEvent.DELIVERY_FAILED,
            {"status": "FAILED", "reason": type(error).__name__, "authority_recheck": True},
        )
        raise

    from packages.plugins import default_plugin_runtime

    adapter = default_plugin_runtime().task_delivery_adapter(provider)
    if adapter is None:
        error = TaskDeliveryError(f"task_delivery_adapter_unavailable:{provider}")
        await _record_workspace_delivery_event(
            delivery_target,
            RuntimeTraceEvent.DELIVERY_FAILED,
            {"status": "FAILED", "reason": str(error)},
        )
        raise error

    try:
        receipt = await adapter.deliver(delivery_target, str(message or ""))
    except Exception as error:
        await _record_workspace_delivery_event(
            delivery_target,
            RuntimeTraceEvent.DELIVERY_FAILED,
            {"status": "FAILED", "reason": type(error).__name__},
        )
        raise

    if not isinstance(receipt, dict) or str(receipt.get("status") or "").upper() != "VERIFIED":
        error = TaskDeliveryError(f"task_delivery_unverified:{provider}")
        await _record_workspace_delivery_event(
            delivery_target,
            RuntimeTraceEvent.DELIVERY_FAILED,
            {"status": "FAILED", "reason": str(error)},
        )
        raise error

    receipt.setdefault("provider", provider)
    receipt.setdefault("artifact_ids", ids)
    receipt.setdefault("verified_at", datetime.now(timezone.utc).isoformat())
    await _record_workspace_delivery_event(
        delivery_target,
        RuntimeTraceEvent.DELIVERY_VERIFIED,
        {
            "status": "VERIFIED",
            "message_ids": list(receipt.get("message_ids") or []),
            "artifact_ids": ids,
            "verified_at": receipt["verified_at"],
            "authority": receipt.get("authority"),
        },
    )
    return receipt


@dataclass(slots=True)
class OperlyConversationDeliveryAdapter:
    """Persist task output back to an Operly web/personal conversation."""

    providers: tuple[str, ...] = ("web", "operly", "task")

    async def deliver(self, target: dict[str, Any], message: str) -> dict[str, Any]:
        conversation_id = str(target.get("external_conversation_id") or "").strip()
        user_id = str(target.get("user_id") or "").strip()
        tenant_id = str(target.get("tenant_id") or "").strip()
        scope = str(target.get("scope") or "workspace")
        if not conversation_id:
            raise TaskDeliveryError("operly_delivery_conversation_missing")

        async with session_scope() as db:
            if scope == "personal":
                principal = await db.scalar(
                    select(Principal).where(
                        Principal.kind == "human",
                        Principal.user_id == user_id,
                    )
                )
                if principal is None:
                    raise TaskDeliveryError("personal_delivery_principal_missing")
                conversation = await db.scalar(
                    select(PrincipalConversation).where(
                        PrincipalConversation.principal_id == principal.id,
                        PrincipalConversation.external_conversation_id == conversation_id,
                    )
                )
                if conversation is None:
                    raise TaskDeliveryError("personal_delivery_conversation_missing")
                row = PrincipalMessage(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=message[:24_000],
                )
                db.add(row)
                await db.flush()
                return {
                    "status": "VERIFIED",
                    "provider": "operly",
                    "message_ids": [row.id],
                    "conversation_id": conversation.id,
                    "artifact_ids": list(target.get("artifact_ids") or []),
                    "authority": {"owner_type": "personal", "owner_id": user_id},
                }

            conversation = await db.scalar(
                select(AgentConversation).where(
                    AgentConversation.id == conversation_id,
                    AgentConversation.tenant_id == tenant_id,
                )
            )
            if conversation is None:
                raise TaskDeliveryError("workspace_delivery_conversation_missing")
            row = AgentMessage(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                role="assistant",
                content=message[:24_000],
            )
            db.add(row)
            await db.flush()
            return {
                "status": "VERIFIED",
                "provider": "operly",
                "message_ids": [row.id],
                "conversation_id": conversation.id,
                "artifact_ids": list(target.get("artifact_ids") or []),
                "authority": {"owner_type": "workspace", "owner_id": tenant_id},
            }
