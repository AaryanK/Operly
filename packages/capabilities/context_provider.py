from sqlalchemy import select

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.context.service import ContextScopeError, ContextService
from packages.database.channel_models import ContextRecord


def _read_definition(capability_id: str, name: str, description: str, permission: str):
    return CapabilityDefinition(
        capability_id,
        name,
        description,
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        {"type": "object"},
        risk_level="read_only",
        permissions=(permission,),
        approval_policy=ApprovalPolicy.AUTO,
    )


def _remember_definition(capability_id: str, name: str, description: str, permission: str):
    return CapabilityDefinition(
        capability_id,
        name,
        description,
        {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "kind": {"type": "string"},
            },
            "required": ["content"],
            "additionalProperties": False,
        },
        {"type": "object"},
        risk_level="low",
        permissions=(permission,),
        reversible=True,
    )


class ContextProvider(BaseProvider):
    name = "operly_context"
    capabilities = (
        _read_definition(
            "context.human.search",
            "context_human_search",
            "Search global private context belonging only to the current linked human. This follows the human across authorized workspaces and surfaces.",
            "context:human:read",
        ),
        _remember_definition(
            "context.human.remember",
            "context_human_remember",
            "Remember a global private fact or preference for the current linked human. Use for person-level facts that should follow them across workspaces and surfaces.",
            "context:human:write",
        ),
        _read_definition(
            "context.private_workspace_search",
            "context_private_workspace_search",
            "Search private context for the current human that is intentionally associated only with the current workspace.",
            "context:human:read",
        ),
        _remember_definition(
            "context.private_workspace_remember",
            "context_private_workspace_remember",
            "Remember a private fact for the current human that should apply only inside the current workspace.",
            "context:human:write",
        ),
        _read_definition(
            "context.tenant.search",
            "context_tenant_search",
            "Search shared context for the current authorized tenant.",
            "context:tenant:read",
        ),
        _remember_definition(
            "context.tenant.remember",
            "context_tenant_remember",
            "Remember a fact that should be shared with authorized members of the current tenant.",
            "context:tenant:write",
        ),
        _read_definition(
            "context.conversation.search",
            "context_conversation_search",
            "Search scoped context for the current conversation.",
            "context:conversation:read",
        ),
        _remember_definition(
            "context.conversation.remember_private",
            "context_conversation_remember_private",
            "Remember a fact privately for the current human in this conversation.",
            "context:conversation:write",
        ),
        _remember_definition(
            "context.conversation.remember_shared",
            "context_conversation_remember_shared",
            "Remember a fact shared with authorized members participating in this tenant conversation.",
            "context:tenant:write",
        ),
    )

    @staticmethod
    def _runtime(context):
        invocation = context.invocation or {}
        metadata = invocation.get("metadata") or {}
        return invocation, metadata

    async def execute(self, context, capability_name, arguments):
        invocation, metadata = self._runtime(context)
        query = str(arguments.get("query") or "")
        content = str(arguments.get("content") or "")
        kind = str(arguments.get("kind") or "fact")[:50]
        user_id = context.actor_id
        conversation_id = str(metadata.get("_conversation_id") or "")
        channel = str(invocation.get("channel") or "")
        space_id = metadata.get("external_space_id")
        source_message_id = metadata.get("external_message_id")

        try:
            if capability_name == "context.human.search":
                rows = await ContextService.search_human(
                    context.db,
                    user_id=user_id or "",
                    tenant_id=None,
                    query=query,
                )
                return CapabilityResult(
                    True,
                    False,
                    {"matches": [{"id": row.id, "kind": row.kind, "content": row.content} for row in rows], "scope": "human_global"},
                )

            if capability_name == "context.human.remember":
                row = await ContextService.remember_human(
                    context.db,
                    user_id=user_id or "",
                    tenant_id=None,
                    content=content,
                    kind=kind,
                    channel_provider=channel or None,
                    channel_space_id=str(space_id) if space_id is not None else None,
                    source_message_id=str(source_message_id) if source_message_id is not None else None,
                )
                return CapabilityResult(True, True, {"context_id": row.id, "scope": "human_global"}, row.id)

            if capability_name == "context.private_workspace_search":
                statement = select(ContextRecord).where(
                    ContextRecord.scope_type == "human",
                    ContextRecord.visibility == "private",
                    ContextRecord.owner_user_id == (user_id or ""),
                    ContextRecord.tenant_id == context.tenant_id,
                )
                statement = ContextService._ranked_query(statement, query).limit(12)
                rows = (await context.db.scalars(statement)).all()
                return CapabilityResult(
                    True,
                    False,
                    {"matches": [{"id": row.id, "kind": row.kind, "content": row.content} for row in rows], "scope": "human_workspace_private"},
                )

            if capability_name == "context.private_workspace_remember":
                row = await ContextService.remember_human(
                    context.db,
                    user_id=user_id or "",
                    tenant_id=context.tenant_id,
                    content=content,
                    kind=kind,
                    channel_provider=channel or None,
                    channel_space_id=str(space_id) if space_id is not None else None,
                    source_message_id=str(source_message_id) if source_message_id is not None else None,
                )
                return CapabilityResult(True, True, {"context_id": row.id, "scope": "human_workspace_private"}, row.id)

            if capability_name == "context.tenant.search":
                rows = await ContextService.search_tenant(
                    context.db,
                    tenant_id=context.tenant_id,
                    query=query,
                )
                return CapabilityResult(
                    True,
                    False,
                    {"matches": [{"id": row.id, "kind": row.kind, "content": row.content} for row in rows]},
                )

            if capability_name == "context.tenant.remember":
                row = await ContextService.remember_tenant(
                    context.db,
                    tenant_id=context.tenant_id,
                    content=content,
                    kind=kind,
                    channel_provider=channel or None,
                    channel_space_id=str(space_id) if space_id is not None else None,
                    source_message_id=str(source_message_id) if source_message_id is not None else None,
                )
                return CapabilityResult(True, True, {"context_id": row.id, "scope": "tenant"}, row.id)

            if capability_name == "context.conversation.search":
                if not conversation_id:
                    return CapabilityResult(False, False, {"reason": "conversation_context_unavailable"})
                rows = await ContextService.search_conversation(
                    context.db,
                    tenant_id=context.tenant_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    query=query,
                )
                return CapabilityResult(
                    True,
                    False,
                    {"matches": [{"id": row.id, "kind": row.kind, "content": row.content} for row in rows]},
                )

            if capability_name in {
                "context.conversation.remember_private",
                "context.conversation.remember_shared",
            }:
                if not conversation_id:
                    return CapabilityResult(False, False, {"reason": "conversation_context_unavailable"})
                private = capability_name.endswith("_private")
                row = await ContextService.remember_conversation(
                    context.db,
                    tenant_id=context.tenant_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    private=private,
                    content=content,
                    kind=kind,
                    channel_provider=channel or None,
                    channel_space_id=str(space_id) if space_id is not None else None,
                    source_message_id=str(source_message_id) if source_message_id is not None else None,
                )
                return CapabilityResult(
                    True,
                    True,
                    {"context_id": row.id, "scope": "conversation", "visibility": "private" if private else "shared"},
                    row.id,
                )
        except ContextScopeError as error:
            return CapabilityResult(False, False, {"reason": str(error)})

        return CapabilityResult(False, False, {"reason": "unsupported_context_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if not result.success:
            return CapabilityResult(False, result.changed, result.evidence, result.external_reference)
        if capability_name.endswith(".search") or capability_name.endswith("_search"):
            return CapabilityResult(True, False, {"observation_available": True, **result.evidence})
        row = await context.db.scalar(
            select(ContextRecord).where(ContextRecord.id == result.external_reference)
        )
        return CapabilityResult(
            row is not None,
            result.changed,
            {"record_exists": row is not None, **result.evidence},
            result.external_reference,
        )
