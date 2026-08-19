from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from packages.business_brain import AgentInput, get_agent_service
from packages.capabilities.time_context import user_time_context
from packages.channels.envelope import ChannelEnvelope, ChannelResponse
from packages.channels.identity import IdentityService
from packages.database.models import Tenant


@dataclass(slots=True)
class TenantResolution:
    tenant_id: str | None
    role: str | None
    user_id: str | None
    allow_tenant_context: bool
    options: list[dict[str, str]]


class ChannelService:
    """Resolve channel events into one canonical AgentInput."""

    @staticmethod
    def _mentions_tenant(text: str, tenant: Tenant) -> bool:
        haystack = " ".join(str(text or "").lower().split())
        candidates = [tenant.name, tenant.slug or ""]
        return any(
            value and " ".join(value.lower().split()) in haystack
            for value in candidates
        )

    @classmethod
    async def _resolve_direct_tenant(
        cls,
        db: AsyncSession,
        envelope: ChannelEnvelope,
        *,
        user_id: str,
    ) -> TenantResolution:
        memberships = await IdentityService.memberships(db, user_id=user_id)
        if not memberships:
            return TenantResolution(None, None, user_id, False, [])

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
        if len(explicit) == 1:
            membership, tenant = explicit[0]
        elif state and state.active_tenant_id:
            match = next(
                (
                    item
                    for item in memberships
                    if item[0].tenant_id == state.active_tenant_id
                ),
                None,
            )
            if match:
                membership, tenant = match
            elif len(memberships) == 1:
                membership, tenant = memberships[0]
            else:
                return TenantResolution(
                    None,
                    None,
                    user_id,
                    False,
                    [
                        {"id": item_tenant.id, "name": item_tenant.name}
                        for _, item_tenant in memberships
                    ],
                )
        elif len(memberships) == 1:
            membership, tenant = memberships[0]
        else:
            return TenantResolution(
                None,
                None,
                user_id,
                False,
                [
                    {"id": item_tenant.id, "name": item_tenant.name}
                    for _, item_tenant in memberships
                ],
            )

        await IdentityService.upsert_conversation_state(
            db,
            provider=envelope.provider,
            external_user_id=envelope.external_user_id,
            external_conversation_id=envelope.external_conversation_id,
            user_id=user_id,
            active_tenant_id=tenant.id,
            metadata={"direct": True},
        )
        return TenantResolution(
            tenant.id,
            membership.role,
            user_id,
            True,
            [],
        )

    @classmethod
    async def resolve(
        cls,
        db: AsyncSession,
        envelope: ChannelEnvelope,
    ) -> TenantResolution:
        identity = await IdentityService.resolve_external_identity(
            db,
            provider=envelope.provider,
            external_user_id=envelope.external_user_id,
        )
        user_id = identity.user_id if identity else None

        if envelope.is_direct:
            if not user_id:
                return TenantResolution(None, None, None, False, [])
            return await cls._resolve_direct_tenant(
                db,
                envelope,
                user_id=user_id,
            )

        if not envelope.external_space_id:
            return TenantResolution(None, None, user_id, False, [])

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

        time_metadata: dict[str, str] = {}
        async with session_scope() as db:
            resolved = await cls.resolve(db, envelope)
            if envelope.is_direct and resolved.user_id is None:
                return ChannelResponse(
                    message=(
                        "Link this communication account to Operly before using "
                        "private DMs. Use the channel's Operly link command or "
                        "connect the account from Operly Settings."
                    ),
                    status="link_required",
                )

            if resolved.tenant_id is None:
                if resolved.options:
                    names = ", ".join(item["name"] for item in resolved.options)
                    return ChannelResponse(
                        message=(
                            "Which Operly workspace do you mean? "
                            f"I can use: {names}. Mention the workspace name and try again."
                        ),
                        user_id=resolved.user_id,
                        status="tenant_required",
                        tenant_options=resolved.options,
                    )
                return ChannelResponse(
                    message="I could not resolve an Operly workspace for this conversation.",
                    user_id=resolved.user_id,
                    status="tenant_required",
                )

            # A server can be installed before individual people link accounts. The
            # provisional tenant remains installed, but private business data and
            # plugins are unavailable until membership is proven.
            if not resolved.allow_tenant_context:
                return ChannelResponse(
                    message=(
                        "This space is connected to Operly, but your identity is not "
                        "linked to a member of this workspace yet. Link your account "
                        "to use business context and actions."
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
            tenant = await db.get(Tenant, resolved.tenant_id)
            requested_timezone = str(envelope.metadata.get("user_timezone") or "").strip()
            time_metadata = user_time_context(
                requested_timezone or (tenant.timezone if tenant else None)
            )

        existing_dashboard = envelope.metadata.get("dashboard_context")
        dashboard_context = (
            dict(existing_dashboard)
            if isinstance(existing_dashboard, dict)
            else {}
        )
        dashboard_context["user_time"] = time_metadata

        result = await get_agent_service().run(
            AgentInput(
                tenant_id=resolved.tenant_id,
                principal_id=(
                    f"user:{resolved.user_id}"
                    if resolved.user_id
                    else f"{envelope.provider}-user:{envelope.external_user_id}"
                ),
                actor_name=envelope.actor_name,
                channel=envelope.provider,
                conversation_id=conversation_id,
                text=envelope.text,
                images=list(envelope.images),
                metadata={
                    **dict(envelope.metadata),
                    **time_metadata,
                    "dashboard_context": dashboard_context,
                    "user_id": resolved.user_id,
                    "role": resolved.role,
                    "allow_tenant_context": resolved.allow_tenant_context,
                    "external_user_id": envelope.external_user_id,
                    "external_space_id": envelope.external_space_id,
                    "external_conversation_id": envelope.external_conversation_id,
                    "is_direct": envelope.is_direct,
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
                metadata={"direct": envelope.is_direct},
            )

        return ChannelResponse(
            message=result["message"],
            conversation_id=result.get("conversation_id"),
            tenant_id=resolved.tenant_id,
            user_id=resolved.user_id,
            role=resolved.role,
        )
