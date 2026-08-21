from sqlalchemy import desc, select

from packages.database.agent_models import AgentMessage
from packages.database.models import Tenant


async def load_conversation_messages(
    db,
    tenant_id: str,
    conversation_id: str,
    limit: int = 24,
) -> list[dict]:
    rows = (
        await db.scalars(
            select(AgentMessage)
            .where(
                AgentMessage.tenant_id == tenant_id,
                AgentMessage.conversation_id == conversation_id,
            )
            .order_by(desc(AgentMessage.created_at))
            .limit(limit)
        )
    ).all()
    rows.reverse()

    messages = []
    for row in rows:
        if row.role not in {"user", "assistant"}:
            continue
        messages.append({"role": row.role, "content": row.content})
    return messages


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
            "</workspace_boundary>",
        ]
    )
