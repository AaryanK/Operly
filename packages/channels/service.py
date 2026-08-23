from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from packages.business_brain import AgentInput, get_agent_service
from packages.business_brain.conversation_artifacts import artifact_context, recent_artifacts
from packages.business_brain.personal_agent import get_personal_agent_service
from packages.channels.envelope import ChannelEnvelope, ChannelResponse
from packages.channels.guest_chat import get_guest_conversation_service
from packages.channels.identity import IdentityService
from packages.database.agent_models import AgentConversation
from packages.database.models import AppUser, Tenant
from packages.security.principals import PrincipalService


@dataclass(slots=True)
class TenantResolution:
    tenant_id: str | None
    role: str | None
    user_id: str | None
    allow_tenant_context: bool
    options: list[dict[str, str]]


class ChannelService:
    """Resolve channels into private-human or shared-workspace execution."""

    @staticmethod
    def _mentions_tenant(text: str, tenant: Tenant) -> bool:
        haystack = " ".join(str(text or "").lower().split())
        candidates = [tenant.name, tenant.slug or ""]
        return any(value and " ".join(value.lower().split()) in haystack for value in candidates)

    @classmethod
    async def _resolve_direct_tenant(cls, db: AsyncSession, envelope: ChannelEnvelope, *, user_id: str) -> TenantResolution:
        """Resolve an optional workspace focus for a private DM.

        A private DM always remains account scoped. An explicit workspace reference or
        remembered focus may be supplied to Personal AI as a disambiguation hint, but
        membership alone never turns the DM into a workspace agent and we never choose
        the first membership merely to create an execution anchor.
        """
        memberships = await IdentityService.memberships(db, user_id=user_id)
        options = [{"id": t.id, "name": t.name, "role": m.role} for m, t in memberships]
        if not memberships:
            return TenantResolution(None, None, user_id, False, options)
        state = await IdentityService.conversation_state(
            db,
            provider=envelope.provider,
            external_user_id=envelope.external_user_id,
            external_conversation_id=envelope.external_conversation_id,
        )
        explicit = [
            (membership, tenant)
            for membership, tenant in memberships
            if cls._mentions_tenant(envelope.text, tenant)
        ]
        selected = explicit[0] if len(explicit) == 1 else None
        if selected is None and state and state.active_tenant_id:
            selected = next(
                (item for item in memberships if item[0].tenant_id == state.active_tenant_id),
                None,
            )
        if selected is None:
            return TenantResolution(None, None, user_id, False, options)

        membership, tenant = selected
        workspace_changed = bool(
            state
            and state.active_tenant_id
            and state.active_tenant_id != tenant.id
        )
        await IdentityService.upsert_conversation_state(
            db,
            provider=envelope.provider,
            external_user_id=envelope.external_user_id,
            external_conversation_id=envelope.external_conversation_id,
            user_id=user_id,
            active_tenant_id=tenant.id,
            clear_agent_conversation=workspace_changed,
            metadata={
                "direct": True,
                "personal_scope": True,
                "workspace_focus_only": True,
                "workspace_count": len(memberships),
            },
        )
        return TenantResolution(
            tenant.id,
            membership.role,
            user_id,
            False,
            options,
        )

    @classmethod
    async def resolve(cls, db: AsyncSession, envelope: ChannelEnvelope) -> TenantResolution:
        identity = await IdentityService.resolve_external_identity(
            db, provider=envelope.provider, external_user_id=envelope.external_user_id
        )
        user_id = identity.user_id if identity else None
        if envelope.is_direct:
            if not user_id:
                return TenantResolution(None, None, None, False, [])
            return await cls._resolve_direct_tenant(db, envelope, user_id=user_id)
        if not envelope.external_space_id:
            return TenantResolution(None, None, user_id, False, [])
        installation = await IdentityService.installation(
            db, provider=envelope.provider, external_space_id=envelope.external_space_id
        )
        if installation is None:
            return TenantResolution(None, None, user_id, False, [])
        membership = await IdentityService.membership(
            db, user_id=user_id, tenant_id=installation.tenant_id
        )
        return TenantResolution(
            installation.tenant_id,
            membership.role if membership else "guest",
            user_id,
            bool(membership),
            [],
        )

    @classmethod
    async def handle(cls, envelope: ChannelEnvelope) -> ChannelResponse:
        from packages.database.db import session_scope

        attachment_prompt = ""
        attachment_names: list[str] = []
        async with session_scope() as db:
            resolved = await cls.resolve(db, envelope)
            if envelope.is_direct and resolved.user_id is None:
                guest = await PrincipalService.resolve_or_create_guest(
                    db,
                    provider=envelope.provider,
                    provider_subject=envelope.external_user_id,
                    display_name=envelope.actor_name,
                )
                conversation, answer = await get_guest_conversation_service().reply(
                    db,
                    principal=guest,
                    provider=envelope.provider,
                    external_conversation_id=envelope.external_conversation_id,
                    text=envelope.text,
                )
                await IdentityService.upsert_conversation_state(
                    db,
                    provider=envelope.provider,
                    external_user_id=envelope.external_user_id,
                    external_conversation_id=envelope.external_conversation_id,
                    user_id=None,
                    active_tenant_id=None,
                    metadata={
                        "direct": True,
                        "guest_principal_id": guest.id,
                        "principal_conversation_id": conversation.id,
                    },
                )
                await db.commit()
                return ChannelResponse(
                    message=answer,
                    conversation_id=conversation.id,
                    status="guest",
                )

            if envelope.is_direct and resolved.user_id:
                # A linked private DM is always the person's account-scoped Personal
                # AI. A resolved workspace is only a focus/disambiguation hint for
                # account-authorized reads; it is not the root execution scope.
                user = await db.get(AppUser, resolved.user_id)
                display_name = user.display_name if user else envelope.actor_name
                await IdentityService.upsert_conversation_state(
                    db,
                    provider=envelope.provider,
                    external_user_id=envelope.external_user_id,
                    external_conversation_id=envelope.external_conversation_id,
                    user_id=resolved.user_id,
                    active_tenant_id=resolved.tenant_id,
                    clear_agent_conversation=True,
                    metadata={
                        "direct": True,
                        "personal_scope": True,
                        "workspace_focus_only": bool(resolved.tenant_id),
                    },
                )
                await db.commit()
                result = await get_personal_agent_service().run(
                    user_id=resolved.user_id,
                    display_name=display_name,
                    message=envelope.text,
                    conversation_id=f"{envelope.provider}:{envelope.external_conversation_id}",
                    selected_workspace_id=resolved.tenant_id,
                )
                return ChannelResponse(
                    message=result["message"],
                    conversation_id=result.get("conversation_id"),
                    user_id=resolved.user_id,
                    status="ok",
                )

            if resolved.tenant_id is None:
                return ChannelResponse(
                    message="This channel space is not bound to an Operly workspace yet. Connect it through an explicit workspace installation flow first.",
                    user_id=resolved.user_id,
                    status="tenant_required",
                )

            if not resolved.allow_tenant_context:
                return ChannelResponse(
                    message="This space is connected to Operly, but your identity is not linked to a member of this workspace yet. Link your account to use business context and actions.",
                    tenant_id=resolved.tenant_id,
                    user_id=resolved.user_id,
                    role=resolved.role,
                    status="membership_required",
                )

            state = await IdentityService.conversation_state(
                db,
                provider=envelope.provider,
                external_user_id=envelope.external_user_id,
                external_conversation_id=envelope.external_conversation_id,
            )
            conversation_id = state.agent_conversation_id if state else None

            if conversation_id:
                conversation = await db.get(AgentConversation, conversation_id)
                expected_principal = f"user:{resolved.user_id}"
                if (
                    conversation is None
                    or conversation.tenant_id != resolved.tenant_id
                    or conversation.principal_id != expected_principal
                    or conversation.channel != envelope.provider
                ):
                    conversation_id = None
                    await IdentityService.upsert_conversation_state(
                        db,
                        provider=envelope.provider,
                        external_user_id=envelope.external_user_id,
                        external_conversation_id=envelope.external_conversation_id,
                        user_id=resolved.user_id,
                        active_tenant_id=resolved.tenant_id,
                        clear_agent_conversation=True,
                        metadata={"direct": envelope.is_direct, "scope_repaired": True},
                    )

            artifacts = await recent_artifacts(
                db,
                tenant_id=resolved.tenant_id,
                user_id=resolved.user_id,
                actor_external_id=envelope.external_user_id,
                channel=envelope.provider,
                conversation_id=envelope.external_conversation_id,
                is_direct=envelope.is_direct,
                limit=6,
            )
            attachment_prompt, attachment_names = artifact_context(artifacts)
            await db.commit()

        result = await get_agent_service().run(
            AgentInput(
                tenant_id=resolved.tenant_id,
                principal_id=f"user:{resolved.user_id}",
                actor_name=envelope.actor_name,
                channel=envelope.provider,
                conversation_id=conversation_id,
                text=envelope.text,
                images=list(envelope.images),
                attachment_context=attachment_prompt,
                attachment_names=attachment_names,
                metadata={
                    **dict(envelope.metadata),
                    "user_id": resolved.user_id,
                    "role": resolved.role,
                    "allow_tenant_context": resolved.allow_tenant_context,
                    "external_user_id": envelope.external_user_id,
                    "external_space_id": envelope.external_space_id,
                    "external_conversation_id": envelope.external_conversation_id,
                    "is_direct": envelope.is_direct,
                    "accessible_workspaces": [],
                    "dm_execution_anchor": None,
                    "retained_artifact_count": len(attachment_names) if attachment_prompt else 0,
                },
            )
        )

        async with session_scope() as db:
            await IdentityService.upsert_conversation_state(
                db,
                provider=envelope.provider,
                external_user_id=envelope.external_user_id,
                external_conversation_id=envelope.external_conversation_id,
                user_id=resolved.user_id,
                active_tenant_id=resolved.tenant_id,
                agent_conversation_id=result.get("conversation_id"),
                metadata={
                    "direct": False,
                    "workspace_scope": True,
                    "retained_artifacts": bool(attachment_prompt),
                },
            )

        return ChannelResponse(
            message=result["message"],
            conversation_id=result.get("conversation_id"),
            tenant_id=resolved.tenant_id,
            user_id=resolved.user_id,
            role=resolved.role,
        )
