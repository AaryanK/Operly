from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select

from packages.database.agent_models import AgentConversation, AgentMessage
from packages.database.channel_models import ExternalIdentity
from packages.database.db import session_scope
from packages.database.models import AppUser
from packages.database.principal_models import Principal, PrincipalConversation, PrincipalMessage


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


async def deliver_task_output(target: dict[str, Any], message: str) -> dict[str, Any]:
    provider = str(target.get("provider") or "").strip().lower()
    if not provider:
        raise TaskDeliveryError("task_delivery_provider_missing")
    from packages.plugins import default_plugin_runtime

    adapter = default_plugin_runtime().task_delivery_adapter(provider)
    if adapter is None:
        raise TaskDeliveryError(f"task_delivery_adapter_unavailable:{provider}")
    receipt = await adapter.deliver(dict(target), str(message or ""))
    if not isinstance(receipt, dict) or str(receipt.get("status") or "").upper() != "VERIFIED":
        raise TaskDeliveryError(f"task_delivery_unverified:{provider}")
    receipt.setdefault("provider", provider)
    receipt.setdefault("verified_at", datetime.now(timezone.utc).isoformat())
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
                "authority": {"owner_type": "workspace", "owner_id": tenant_id},
            }
