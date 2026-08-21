from sqlalchemy import desc, or_, select

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.database.agent_models import AgentConversation, AgentMessage


class ConversationHistoryProvider(BaseProvider):
    """Retrieval over persisted Operly conversation history without prompt stuffing."""

    name = "operly_history"
    capabilities = (
        CapabilityDefinition(
            "conversation.search_history",
            "conversation_search_history",
            "Search persisted Operly conversation messages for the current authenticated principal inside the current workspace. Use this before claiming to have searched older Operly history.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("messages:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "conversation.read_recent",
            "conversation_read_recent",
            "Read a bounded set of persisted user/assistant messages from the current Operly conversation, including messages outside the normal short model window when requested.",
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("messages:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
    )

    @staticmethod
    def _row(row: AgentMessage) -> dict:
        return {
            "message_id": row.id,
            "conversation_id": row.conversation_id,
            "role": row.role,
            "content": row.content[:4000],
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    async def execute(self, context, capability_name, arguments):
        invocation = context.invocation or {}
        metadata = invocation.get("metadata") if isinstance(invocation.get("metadata"), dict) else {}
        principal_id = str(metadata.get("principal_id") or "").strip()
        conversation_id = str(metadata.get("_conversation_id") or "").strip()
        if not principal_id:
            return CapabilityResult(False, False, {"reason": "principal_unavailable"})

        if capability_name == "conversation.read_recent":
            if not conversation_id:
                return CapabilityResult(False, False, {"reason": "conversation_unavailable"})
            limit = max(1, min(int(arguments.get("limit", 50)), 100))
            rows = (
                await context.db.scalars(
                    select(AgentMessage)
                    .join(AgentConversation, AgentConversation.id == AgentMessage.conversation_id)
                    .where(
                        AgentMessage.tenant_id == context.tenant_id,
                        AgentMessage.conversation_id == conversation_id,
                        AgentConversation.principal_id == principal_id,
                        AgentMessage.role.in_(("user", "assistant")),
                    )
                    .order_by(desc(AgentMessage.created_at))
                    .limit(limit)
                )
            ).all()
            rows.reverse()
            return CapabilityResult(True, False, {"messages": [self._row(row) for row in rows]})

        if capability_name == "conversation.search_history":
            query = " ".join(str(arguments.get("query") or "").split()).strip()
            if not query:
                return CapabilityResult(False, False, {"reason": "query_required"})
            limit = max(1, min(int(arguments.get("limit", 20)), 50))
            terms = [term for term in query.split() if len(term) >= 3][:8]
            statement = (
                select(AgentMessage)
                .join(AgentConversation, AgentConversation.id == AgentMessage.conversation_id)
                .where(
                    AgentMessage.tenant_id == context.tenant_id,
                    AgentConversation.tenant_id == context.tenant_id,
                    AgentConversation.principal_id == principal_id,
                    AgentMessage.role.in_(("user", "assistant")),
                )
            )
            if terms:
                statement = statement.where(or_(*[AgentMessage.content.ilike(f"%{term}%") for term in terms]))
            rows = (await context.db.scalars(statement.order_by(desc(AgentMessage.created_at)).limit(limit))).all()
            return CapabilityResult(True, False, {"query": query, "matches": [self._row(row) for row in rows]})

        return CapabilityResult(False, False, {"reason": "unsupported_history_capability"})

    async def verify(self, context, capability_name, arguments, result):
        del context, capability_name, arguments
        return CapabilityResult(result.success, False, {"observation_available": result.success, **result.evidence})
