from uuid import uuid4

from packages.business_brain.runtime_v2 import run_workspace_runtime_v2
from packages.business_brain.security import (
    MAX_ASSISTANT_TEXT,
    MAX_USER_TEXT,
    AgentSecurityError,
    SlidingWindowRateLimiter,
    bounded_text,
)
from packages.business_brain.types import AgentInput
from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext
from packages.database.agent_models import AgentConversation, AgentMessage
from packages.database.db import session_scope
from packages.security.execution_context import ExecutionContextError, resolve_execution_context
from packages.security.surfaces import SurfaceKind


class AgentService:
    """Canonical Workspace/Guest Workspace Agent Runtime v2 service."""

    def __init__(self) -> None:
        self.plugin_harness = PluginAgentHarness()
        self.rate_limiter = SlidingWindowRateLimiter(limit=20, window_seconds=60)

    @staticmethod
    def _surface_for(request: AgentInput) -> SurfaceKind:
        explicit = SurfaceKind.coerce(request.metadata.get("_surface_kind"))
        if explicit in {
            SurfaceKind.WORKSPACE_SHARED,
            SurfaceKind.WORKSPACE_PRIVATE,
            SurfaceKind.DISCORD_GUILD,
            SurfaceKind.SYSTEM_TASK,
        }:
            return explicit
        if bool(request.metadata.get("is_direct")):
            return SurfaceKind.WORKSPACE_PRIVATE
        if str(request.channel or "").strip().lower() == "discord":
            return SurfaceKind.DISCORD_GUILD
        return SurfaceKind.WORKSPACE_SHARED

    @staticmethod
    async def _persist_assistant(
        *,
        tenant_id: str,
        conversation_id: str,
        answer: str,
    ) -> None:
        async with session_scope() as db:
            db.add(
                AgentMessage(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=answer,
                )
            )

    async def run(self, request: AgentInput) -> dict:
        if not request.tenant_id or not request.principal_id:
            raise AgentSecurityError("Tenant and principal are required")
        if request.images:
            raise ValueError(
                "Raw image payloads are retired. Persist attachments to the Artifact Store before invoking Agent Runtime v2."
            )

        user_text = bounded_text(request.text, MAX_USER_TEXT).strip()
        attachment_context = bounded_text(request.attachment_context, MAX_USER_TEXT).strip()
        if not user_text and not attachment_context:
            raise ValueError("Message is empty")

        objective = user_text or "Work with the uploaded attachment(s)."
        rate_key = f"{request.tenant_id}:{request.principal_id}:{request.channel}"
        await self.rate_limiter.check(rate_key)
        conversation = await self._get_or_create_conversation(request)

        surface_kind = self._surface_for(request)
        request.metadata["_surface_kind"] = surface_kind.value
        request.metadata["shared_surface"] = surface_kind.is_shared
        request.metadata["principal_id"] = request.principal_id

        user_id = str(request.metadata.get("user_id") or "").strip() or None
        if request.principal_id.startswith("guest:"):
            request.metadata["_guest_principal_id"] = request.principal_id

        try:
            async with session_scope() as db:
                execution = await resolve_execution_context(
                    db,
                    workspace_id=request.tenant_id,
                    user_id=user_id,
                    channel=request.channel,
                    surface=surface_kind,
                    conversation_id=conversation.id,
                    metadata=request.metadata,
                    require_membership=True,
                )
        except ExecutionContextError as error:
            raise AgentSecurityError(str(error)) from error

        trusted_role = execution.role
        allow_tenant_context = bool(
            request.metadata.get("allow_tenant_context", True)
        ) and (execution.is_member or execution.is_guest_workspace)
        surface_kind = execution.surface
        request.metadata["role"] = trusted_role
        request.metadata["allow_tenant_context"] = allow_tenant_context
        request.metadata["_surface_kind"] = surface_kind.value
        request.metadata["shared_surface"] = surface_kind.is_shared
        request.metadata["workspace_mode"] = execution.workspace_mode
        request.metadata["effective_permissions"] = sorted(execution.permissions)

        attachment_label = ""
        if request.attachment_names:
            attachment_label = " [Attachments: " + ", ".join(request.attachment_names[:10]) + "]"
        stored_user_text = (user_text or "Uploaded attachment(s)") + attachment_label
        async with session_scope() as db:
            db.add(
                AgentMessage(
                    tenant_id=request.tenant_id,
                    conversation_id=conversation.id,
                    role="user",
                    content=stored_user_text,
                )
            )

        plugin_metadata = dict(request.metadata)
        plugin_metadata["_conversation_id"] = conversation.id
        plugin_metadata["allow_tenant_context"] = allow_tenant_context
        plugin_metadata["_surface_kind"] = surface_kind.value
        plugin_metadata["principal_id"] = request.principal_id
        plugin_context = PluginInvocationContext(
            tenant_id=request.tenant_id,
            user_id=user_id,
            role=trusted_role,
            objective=objective,
            channel=request.channel,
            metadata=plugin_metadata,
            surface=surface_kind,
            principal_id=request.principal_id,
        )

        run = await run_workspace_runtime_v2(
            objective=objective,
            request=request,
            conversation_id=conversation.id,
            execution=execution,
            plugin_harness=self.plugin_harness,
            plugin_context=plugin_context,
        )
        answer = bounded_text(
            run.get("message") or "Done.",
            MAX_ASSISTANT_TEXT,
        ).strip()
        await self._persist_assistant(
            tenant_id=request.tenant_id,
            conversation_id=conversation.id,
            answer=answer,
        )
        return {
            "conversation_id": conversation.id,
            "message": answer,
            "stop_reason": run.get("stop_reason"),
            "runtime_run_id": run.get("runtime_run_id"),
            "replans": 0,
            "run_plan": run.get("run_plan"),
            "execution_truth": run.get("execution_truth"),
            "runtime_v2": run.get("runtime_v2"),
            "runtime_controller": "agent_runtime_v2",
        }

    async def _get_or_create_conversation(self, request: AgentInput) -> AgentConversation:
        conversation_id = request.conversation_id or str(uuid4())
        async with session_scope() as db:
            row = await db.get(AgentConversation, conversation_id)
            if row is not None:
                if (
                    row.tenant_id != request.tenant_id
                    or row.principal_id != request.principal_id
                    or row.channel != request.channel
                ):
                    raise AgentSecurityError(
                        "Conversation does not belong to this security principal"
                    )
                return row

            title_source = request.text.strip()
            if not title_source and request.attachment_names:
                title_source = "Attachments: " + ", ".join(request.attachment_names[:3])
            row = AgentConversation(
                id=conversation_id,
                tenant_id=request.tenant_id,
                principal_id=request.principal_id,
                channel=request.channel,
                title=(title_source[:80] or "OPERLY conversation"),
            )
            db.add(row)
            await db.flush()
            return row


_AGENT_SERVICE: AgentService | None = None


def get_agent_service() -> AgentService:
    global _AGENT_SERVICE
    if _AGENT_SERVICE is None:
        _AGENT_SERVICE = AgentService()
    return _AGENT_SERVICE
