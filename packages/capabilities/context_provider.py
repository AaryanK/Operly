from sqlalchemy import select

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.context.broker import ContextBroker
from packages.context.federation import FederatedHistoryService
from packages.context.service import ContextScopeError, ContextService
from packages.database.channel_models import ContextRecord
from packages.security.surfaces import SurfaceKind, capability_surface_allowed


def _read_definition(capability_id: str, name: str, description: str, permission: str):
    return CapabilityDefinition(
        capability_id, name, description,
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False},
        {"type": "object"}, risk_level="read_only", permissions=(permission,), approval_policy=ApprovalPolicy.AUTO,
    )


def _remember_definition(capability_id: str, name: str, description: str, permission: str):
    return CapabilityDefinition(
        capability_id, name, description,
        {"type": "object", "properties": {"content": {"type": "string"}, "kind": {"type": "string"}}, "required": ["content"], "additionalProperties": False},
        {"type": "object"}, risk_level="low", permissions=(permission,), reversible=True,
    )


class ContextProvider(BaseProvider):
    name = "operly_context"
    capabilities = (
        CapabilityDefinition(
            "context.search", "context_search",
            "Search the complete history currently authorized to this surface and return compact references. On Personal AI this federates Operly context, private conversations, every authorized workspace/channel history, events, and searchable connected provider accounts such as Gmail. Use context.get only for references whose contents you need in this model.",
            {"type": "object", "properties": {"query": {"type": "string", "maxLength": 1000}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["query"], "additionalProperties": False},
            {"type": "object"}, risk_level="read_only", approval_policy=ApprovalPolicy.AUTO,
            category="context", tags=frozenset({"kernel", "context", "retrieval", "federated-history"}),
            semantic_operations=frozenset({"find context", "find memory", "retrieve knowledge", "search history", "search across accounts", "search email history"}),
        ),
        CapabilityDefinition(
            "context.get", "context_get",
            "Materialize exact contents of authorized federated history references into the current model. References are re-authorized against their current workspace or provider account on every read.",
            {"type": "object", "properties": {"refs": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12}}, "required": ["refs"], "additionalProperties": False},
            {"type": "object"}, risk_level="read_only", approval_policy=ApprovalPolicy.AUTO,
            category="context", tags=frozenset({"kernel", "context", "retrieval", "federated-history"}),
            semantic_operations=frozenset({"read context ref", "materialize context", "expand memory", "read history ref"}),
        ),
        _read_definition("context.human.search", "context_human_search", "Search global private context belonging only to the current linked human.", "context:human:read"),
        _remember_definition("context.human.remember", "context_human_remember", "Remember a global private fact or preference for the current linked human.", "context:human:write"),
        _read_definition("context.private_workspace_search", "context_private_workspace_search", "Search private context for the current human associated only with the current workspace.", "context:human:read"),
        _remember_definition("context.private_workspace_remember", "context_private_workspace_remember", "Remember a private fact for the current human that applies only inside the current workspace.", "context:human:write"),
        _read_definition("context.tenant.search", "context_tenant_search", "Search shared context for the current authorized tenant.", "context:tenant:read"),
        _remember_definition("context.tenant.remember", "context_tenant_remember", "Remember a fact that should be shared with authorized members of the current tenant.", "context:tenant:write"),
        _read_definition("context.conversation.search", "context_conversation_search", "Search context for the current conversation; private rows are included only on a private surface.", "context:conversation:read"),
        _remember_definition("context.conversation.remember_private", "context_conversation_remember_private", "Remember a fact privately for the current human in this conversation.", "context:conversation:write"),
        _remember_definition("context.conversation.remember_shared", "context_conversation_remember_shared", "Remember a fact shared with authorized members participating in this tenant conversation.", "context:tenant:write"),
    )

    @staticmethod
    def _runtime(context):
        invocation = context.invocation or {}
        metadata = invocation.get("metadata") or {}
        return invocation, metadata

    async def execute(self, context, capability_name, arguments):
        invocation, metadata = self._runtime(context)
        authority = set(invocation.get("authority") or [])
        surface = SurfaceKind.coerce(
            metadata.get("_surface_kind") or invocation.get("surface")
        )
        query = str(arguments.get("query") or "")
        content = str(arguments.get("content") or "")
        kind = str(arguments.get("kind") or "fact")[:50]
        user_id = context.actor_id
        conversation_id = str(metadata.get("_conversation_id") or "")
        channel = str(invocation.get("channel") or "")
        space_id = metadata.get("external_space_id")
        source_message_id = metadata.get("external_message_id")

        if not capability_surface_allowed(capability_name, surface):
            return CapabilityResult(False, False, {"reason": "surface_hidden"})

        try:
            if capability_name == "context.search":
                refs = await FederatedHistoryService.search(
                    context,
                    tenant_id=context.tenant_id,
                    user_id=user_id,
                    conversation_id=conversation_id or None,
                    authority=authority,
                    surface=surface,
                    query=query,
                    limit=int(arguments.get("limit") or 8),
                )
                return CapabilityResult(
                    True,
                    False,
                    {
                        "refs": [item.as_dict() for item in refs],
                        "count": len(refs),
                        "surface": surface.value,
                        "contents_materialized": False,
                        "federated": True,
                        "semantic_backend": ContextBroker.semantic_backend_name(),
                        "semantic_degraded_reason": ContextBroker.semantic_degraded_reason(),
                        "ranked_refs": [item.id for item in refs],
                        "sources": sorted({item.source for item in refs}),
                        "estimated_tokens_if_all_materialized": sum(item.estimated_tokens for item in refs),
                    },
                )

            if capability_name == "context.get":
                requested_refs = [str(item) for item in arguments.get("refs") or ()]
                rows = await FederatedHistoryService.materialize(
                    context,
                    refs=requested_refs,
                    tenant_id=context.tenant_id,
                    user_id=user_id,
                    conversation_id=conversation_id or None,
                    authority=authority,
                    surface=surface,
                )
                return CapabilityResult(
                    True,
                    False,
                    {
                        "contexts": rows,
                        "count": len(rows),
                        "requested_count": len(requested_refs),
                        "surface": surface.value,
                        "federated": True,
                        "references_reauthorized": True,
                        "materialized_refs": [str(row.get("ref") or "") for row in rows],
                        "materialized_sources": sorted({str(row.get("source") or "context") for row in rows}),
                        "materialized_estimated_tokens": sum(int(row.get("estimated_tokens") or 0) for row in rows),
                    },
                )

            if capability_name == "context.human.search":
                rows = await ContextService.search_human(context.db, user_id=user_id or "", tenant_id=None, query=query)
                return CapabilityResult(True, False, {"matches": [{"id": row.id, "kind": row.kind, "content": row.content} for row in rows], "scope": "human_global"})

            if capability_name == "context.human.remember":
                row = await ContextService.remember_human(context.db, user_id=user_id or "", tenant_id=None, content=content, kind=kind, channel_provider=channel or None, channel_space_id=str(space_id) if space_id is not None else None, source_message_id=str(source_message_id) if source_message_id is not None else None)
                return CapabilityResult(True, True, {"context_id": row.id, "scope": "human_global"}, row.id)

            if capability_name == "context.private_workspace_search":
                statement = select(ContextRecord).where(ContextRecord.scope_type == "human", ContextRecord.visibility == "private", ContextRecord.owner_user_id == (user_id or ""), ContextRecord.tenant_id == context.tenant_id)
                rows = (await context.db.scalars(ContextService._ranked_query(statement, query).limit(12))).all()
                return CapabilityResult(True, False, {"matches": [{"id": row.id, "kind": row.kind, "content": row.content} for row in rows], "scope": "human_workspace_private"})

            if capability_name == "context.private_workspace_remember":
                row = await ContextService.remember_human(context.db, user_id=user_id or "", tenant_id=context.tenant_id, content=content, kind=kind, channel_provider=channel or None, channel_space_id=str(space_id) if space_id is not None else None, source_message_id=str(source_message_id) if source_message_id is not None else None)
                return CapabilityResult(True, True, {"context_id": row.id, "scope": "human_workspace_private"}, row.id)

            if capability_name == "context.tenant.search":
                rows = await ContextService.search_tenant(context.db, tenant_id=context.tenant_id, query=query)
                return CapabilityResult(True, False, {"matches": [{"id": row.id, "kind": row.kind, "content": row.content} for row in rows]})

            if capability_name == "context.tenant.remember":
                row = await ContextService.remember_tenant(context.db, tenant_id=context.tenant_id, content=content, kind=kind, channel_provider=channel or None, channel_space_id=str(space_id) if space_id is not None else None, source_message_id=str(source_message_id) if source_message_id is not None else None)
                return CapabilityResult(True, True, {"context_id": row.id, "scope": "tenant"}, row.id)

            if capability_name == "context.conversation.search":
                if not conversation_id:
                    return CapabilityResult(False, False, {"reason": "conversation_context_unavailable"})
                rows = await ContextService.search_conversation(context.db, tenant_id=context.tenant_id, conversation_id=conversation_id, user_id=user_id, query=query, include_private=surface.allows_private_conversation)
                return CapabilityResult(True, False, {"matches": [{"id": row.id, "kind": row.kind, "content": row.content} for row in rows]})

            if capability_name in {"context.conversation.remember_private", "context.conversation.remember_shared"}:
                if not conversation_id:
                    return CapabilityResult(False, False, {"reason": "conversation_context_unavailable"})
                private = capability_name.endswith("_private")
                if private and not surface.allows_private_conversation:
                    return CapabilityResult(False, False, {"reason": "surface_hidden"})
                row = await ContextService.remember_conversation(context.db, tenant_id=context.tenant_id, conversation_id=conversation_id, user_id=user_id, private=private, content=content, kind=kind, channel_provider=channel or None, channel_space_id=str(space_id) if space_id is not None else None, source_message_id=str(source_message_id) if source_message_id is not None else None)
                return CapabilityResult(True, True, {"context_id": row.id, "scope": "conversation", "visibility": "private" if private else "shared"}, row.id)
        except ContextScopeError as error:
            return CapabilityResult(False, False, {"reason": str(error)})
        return CapabilityResult(False, False, {"reason": "unsupported_context_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if not result.success:
            return CapabilityResult(False, result.changed, result.evidence, result.external_reference)
        if capability_name.endswith(".search") or capability_name.endswith("_search") or capability_name == "context.get":
            return CapabilityResult(True, False, {"observation_available": True, **result.evidence})
        row = await context.db.scalar(select(ContextRecord).where(ContextRecord.id == result.external_reference))
        return CapabilityResult(row is not None, result.changed, {"record_exists": row is not None, **result.evidence}, result.external_reference)
