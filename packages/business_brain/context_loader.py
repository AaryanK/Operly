from sqlalchemy import desc, select

from packages.database.agent_models import AgentMessage
from packages.database.models import Tenant


async def load_conversation_messages(
    db,
    tenant_id: str,
    conversation_id: str,
    limit: int = 12,
    max_chars: int = 18_000,
) -> list[dict]:
    """Return the newest conversational tail under both count and size budgets.

    Conversation persistence is not the model's working memory. Older/large history
    stays durable in storage and can be retrieved through governed context paths; it
    is not replayed into every model prefill merely because the conversation is old.
    """
    rows = (
        await db.scalars(
            select(AgentMessage)
            .where(
                AgentMessage.tenant_id == tenant_id,
                AgentMessage.conversation_id == conversation_id,
            )
            .order_by(desc(AgentMessage.created_at))
            .limit(max(1, min(int(limit), 24)))
        )
    ).all()

    remaining = max(1_000, min(int(max_chars), 60_000))
    newest_first: list[dict] = []
    for row in rows:
        if row.role not in {"user", "assistant"}:
            continue
        content = str(row.content or "")
        if not content:
            continue
        if remaining <= 0:
            break
        bounded = content[:remaining]
        newest_first.append({"role": row.role, "content": bounded})
        remaining -= len(bounded)

    newest_first.reverse()
    return newest_first


async def load_business_context(db, tenant_id: str) -> str:
    """Return only the trusted workspace envelope for the model.

    Business records are intentionally *not* preloaded here. The reasoning model
    must retrieve business data through permission-filtered context/capability
    paths so the application, not the model, controls the disclosure boundary.
    """
    tenant = await db.get(Tenant, tenant_id)
    workspace_name = tenant.name if tenant is not None else "Current workspace"
    return "\n".join(
        [
            '<workspace_boundary application_controlled="true">',
            f"Workspace: {workspace_name[:200]}",
            "No business records are automatically included in this envelope.",
            "Use only authorized context and supplied capabilities to retrieve needed data.",
            "Never infer or switch to another workspace.",
            "Working conversation context is intentionally bounded. Use governed context retrieval when older persisted history is needed.",
            "EVIDENCE CONTRACT: Never claim that you searched, read, checked, inspected, listed, sent, changed, approved, rejected, deleted, or verified something unless trusted context or a current-turn capability observation proves that claim. If the needed retrieval capability is unavailable or was not used, say that you do not currently have enough evidence/access rather than claiming you searched and found nothing.",
            "</workspace_boundary>",
        ]
    )
