from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from packages.artifacts.delivery import project_agent_result
from packages.artifacts.service import ArtifactScope
from packages.business_brain import AgentInput, get_agent_service
from packages.business_brain.conversation_artifacts import artifact_context, recent_artifacts
from packages.business_brain.personal_agent import get_personal_agent_service
from packages.channels.envelope import ChannelEnvelope, ChannelResponse
from packages.channels.guest_chat import get_guest_conversation_service
from packages.channels.identity import IdentityService
from packages.database.agent_models import AgentConversation
from packages.database.models import AppUser, Tenant
from packages.security.guest_workspace import resolve_guest_workspace_authority
from packages.security.principals import PrincipalService


@dataclass(slots=True)
class TenantResolution:
    tenant_id: str | None
    role: str | None
    user_id: str | None
    allow_tenant_context: bool
    options: list[dict[str, str]]
    principal_id: str | None = None
    workspace_mode: str = "full"
    installation_id: str | None = None
    effective_permissions: frozenset[str] = frozenset()
    platform_permissions: frozenset[str] = frozenset()
    platform_admin: bool = False

    @property
    def is_guest_workspace(self) -> bool:
        return self.workspace_mode == "guest" and self.tenant_id is not None

    @property
    def can_process_files(self) -> bool:
        return "files:process" in self.effective_permissions


def _workspace_artifact_scope(tenant_id: str) -> ArtifactScope:
    return ArtifactScope("workspace", tenant_id, tenant_id=tenant_id)


def _personal_artifact_scope(user_id: str) -> ArtifactScope:
    return ArtifactScope("personal", f"personal:{user_id}", owner_user_id=user_id)


class ChannelService:
    """Resolve every channel into a user/personal/full-workspace/guest-workspace scope."""

    @staticmethod
    def _mentions_tenant(text: str, tenant: Tenant) -> bool:
        haystack = " ".join(str(text or "").lower().split())
        candidates = [tenant.name, tenant.slug or ""]
        return any(value and " ".join(value.lower().split()) in haystack for value in candidates)

    @staticmethod
    async def _trusted_guest_metadata(envelope: ChannelEnvelope) -> dict:
        """Build reserved Guest Workspace authority fields from connector state.

        Reserved ``_operly_platform_*`` values from an incoming envelope are discarded
        first. A client/model therefore cannot self-assert source-platform authority.
        Each connector must derive these values from its own authenticated runtime.
        """
        metadata = dict(envelope.metadata)
        metadata.pop("_operly_platform_permissions", None)
        metadata.pop("_operly_platform_admin", None)

        if str(envelope.provider or "").strip().lower() == "discord":
            from packages.connectors.discord.authority import resolve_discord_authority

            authority = await resolve_discord_authority(
                {
                    **metadata,
                    "external_space_id": envelope.external_space_id,
                    "external_user_id": envelope.external_user_id,
                }
            )
            if authority is not None:
                metadata["_operly_platform_permissions"] = sorted(authority.permissions)
                metadata["_operly_platform_admin"] = authority.is_admin
        return metadata

    @classmethod
    async def _resolve_direct_tenant(
        cls,
        db: AsyncSession,
        envelope: ChannelEnvelope,
        *,
        user_id: str,
    ) -> TenantResolution:
        memberships = await IdentityService.memberships(db, user_id=user_id)
        options = [{"id": t.id, "name": t.name, "role": m.role} for m, t in memberships]
        if not memberships:
            return TenantResolution(
                None,
                None,
                user_id,
                False,
                options,
                principal_id=f"user:{user_id}",
            )
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
            return TenantResolution(
                None,
                None,
                user_id,
                False,
                options,
                principal_id=f"user:{user_id}",
            )

        membership, tenant = selected
        workspace_changed = bool(
            state and state.active_tenant_id and state.active_tenant_id != tenant.id
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
            principal_id=f"user:{user_id}",
        )

    @classmethod
    async def resolve(cls, db: AsyncSession, envelope: ChannelEnvelope) -> TenantResolution:
        identity = await IdentityService.resolve_external_identity(
            db,
            provider=envelope.provider,
            external_user_id=envelope.external_user_id,
        )
        user_id = identity.user_id if identity else None
        if envelope.is_direct:
            if not user_id:
                return TenantResolution(None, None, None, False, [])
            return await cls._resolve_direct_tenant(db, envelope, user_id=user_id)

        if not envelope.external_space_id:
            return TenantResolution(
                None,
                None,
                user_id,
                False,
                [],
                principal_id=f"user:{user_id}" if user_id else None,
            )

        installation = await IdentityService.ensure_installation(
            db,
            provider=envelope.provider,
            external_space_id=envelope.external_space_id,
            display_name=envelope.space_name,
        )
        membership = await IdentityService.membership(
            db,
            user_id=user_id,
            tenant_id=installation.tenant_id,
        )

        if membership is not None:
            return TenantResolution(
                installation.tenant_id,
                membership.role,
                user_id,
                True,
                [],
                principal_id=f"user:{user_id}",
                workspace_mode="full",
                installation_id=installation.id,
            )

        if installation.provisional:
            if user_id:
                principal_id = f"user:{user_id}"
            else:
                guest = await PrincipalService.resolve_or_create_guest(
                    db,
                    provider=envelope.provider,
                    provider_subject=envelope.external_user_id,
                    display_name=envelope.actor_name,
                )
                principal_id = f"guest:{guest.id}"

            trusted_metadata = await cls._trusted_guest_metadata(envelope)
            guest_authority = await resolve_guest_workspace_authority(
                db,
                workspace_id=installation.tenant_id,
                provider=envelope.provider,
                external_space_id=envelope.external_space_id,
                principal_id=principal_id,
                interaction_metadata=trusted_metadata,
            )
            effective = (
                guest_authority.effective_permissions if guest_authority is not None else frozenset()
            )
            has_attachments = bool(envelope.metadata.get("has_attachments"))
            allow_interaction = not has_attachments or "files:process" in effective
            return TenantResolution(
                installation.tenant_id,
                guest_authority.role if guest_authority is not None else "guest",
                user_id,
                allow_interaction,
                [],
                principal_id=principal_id,
                workspace_mode="guest",
                installation_id=installation.id,
                effective_permissions=frozenset(effective),
                platform_permissions=(
                    guest_authority.platform_permissions
                    if guest_authority is not None
                    else frozenset()
                ),
                platform_admin=bool(guest_authority and guest_authority.platform_admin),
            )

        return TenantResolution(
            installation.tenant_id,
            "guest",
            user_id,
            False,
            [],
            principal_id=f"user:{user_id}" if user_id else None,
            workspace_mode="full",
            installation_id=installation.id,
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
                result = await project_agent_result(
                    db,
                    _personal_artifact_scope(resolved.user_id),
                    result,
                )
                return ChannelResponse(
                    message=result["message"],
                    conversation_id=result.get("conversation_id"),
                    user_id=resolved.user_id,
                    status="ok",
                    artifacts=list(result.get("artifacts") or []),
                )

            if resolved.tenant_id is None:
                return ChannelResponse(
                    message="This interaction has no resolvable workspace scope.",
                    user_id=resolved.user_id,
                    status="workspace_required",
                )

            if not resolved.allow_tenant_context:
                if resolved.is_guest_workspace and envelope.metadata.get("has_attachments"):
                    return ChannelResponse(
                        message=(
                            "This Guest Workspace does not authorize file processing for your current platform identity."
                        ),
                        tenant_id=resolved.tenant_id,
                        user_id=resolved.user_id,
                        role=resolved.role,
                        status="permission_denied",
                    )
                return ChannelResponse(
                    message=(
                        "This external space is attached to a claimed Operly workspace, "
                        "but your identity is not an authorized member."
                    ),
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
                expected_principal = resolved.principal_id
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
                        metadata={
                            "direct": False,
                            "scope_repaired": True,
                            "workspace_mode": resolved.workspace_mode,
                        },
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

        request_metadata = {
            **dict(envelope.metadata),
            "user_id": resolved.user_id,
            "principal_id": resolved.principal_id,
            "role": resolved.role,
            "allow_tenant_context": resolved.allow_tenant_context,
            "external_user_id": envelope.external_user_id,
            "external_space_id": envelope.external_space_id,
            "external_conversation_id": envelope.external_conversation_id,
            "is_direct": False,
            "workspace_mode": resolved.workspace_mode,
            "guest_workspace": resolved.is_guest_workspace,
            "installation_id": resolved.installation_id,
            "accessible_workspaces": [],
            "dm_execution_anchor": None,
            "retained_artifact_count": len(attachment_names) if attachment_prompt else 0,
        }
        # Reserved authority fields are emitted only after the connector-backed resolve
        # above. Original envelope values cannot survive this assignment.
        request_metadata.pop("_operly_platform_permissions", None)
        request_metadata.pop("_operly_platform_admin", None)
        if resolved.is_guest_workspace and resolved.principal_id:
            request_metadata["_guest_principal_id"] = resolved.principal_id
            request_metadata["_operly_platform_permissions"] = sorted(
                resolved.platform_permissions
            )
            request_metadata["_operly_platform_admin"] = resolved.platform_admin

        result = await get_agent_service().run(
            AgentInput(
                tenant_id=resolved.tenant_id,
                principal_id=resolved.principal_id or "",
                actor_name=envelope.actor_name,
                channel=envelope.provider,
                conversation_id=conversation_id,
                text=envelope.text,
                images=list(envelope.images),
                attachment_context=attachment_prompt,
                attachment_names=attachment_names,
                metadata=request_metadata,
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
                    "workspace_mode": resolved.workspace_mode,
                    "guest_principal_id": (
                        resolved.principal_id if resolved.is_guest_workspace else None
                    ),
                    "retained_artifacts": bool(attachment_prompt),
                },
            )
            result = await project_agent_result(
                db,
                _workspace_artifact_scope(resolved.tenant_id),
                result,
            )

        return ChannelResponse(
            message=result["message"],
            conversation_id=result.get("conversation_id"),
            tenant_id=resolved.tenant_id,
            user_id=resolved.user_id,
            role=resolved.role,
            artifacts=list(result.get("artifacts") or []),
        )
