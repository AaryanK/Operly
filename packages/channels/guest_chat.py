from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.business_brain.ollama_client import OllamaClient
from packages.database.principal_models import Principal, PrincipalConversation, PrincipalMessage
from packages.model_runtime.portfolio import model_route


GUEST_SYSTEM_PROMPT = """
You are OPERLY speaking to an unauthenticated guest.
You may explain Operly, help the guest think through general work, and preserve conversational continuity.
You have no workspace data, no business connectors, no private user data, and no tools.
Never imply that the guest is authenticated or a member of any workspace.
If the request requires personal/workspace data or an action, explain that the guest should securely link their Operly account.
Never ask for passwords, tokens, API keys, authentication codes, or secrets in chat.
""".strip()


class GuestConversationService:
    def __init__(self) -> None:
        route = model_route("business_agent")
        self.client = OllamaClient(model=route.primary, fallback_models=route.fallbacks)

    async def conversation(
        self,
        db: AsyncSession,
        *,
        principal: Principal,
        provider: str,
        external_conversation_id: str,
    ) -> PrincipalConversation:
        row = await db.scalar(
            select(PrincipalConversation).where(
                PrincipalConversation.principal_id == principal.id,
                PrincipalConversation.provider == provider,
                PrincipalConversation.external_conversation_id == str(external_conversation_id),
            )
        )
        if row:
            return row
        row = PrincipalConversation(
            principal_id=principal.id,
            provider=provider,
            external_conversation_id=str(external_conversation_id),
            title="Guest conversation",
            status="active",
        )
        db.add(row)
        await db.flush()
        return row

    async def reply(
        self,
        db: AsyncSession,
        *,
        principal: Principal,
        provider: str,
        external_conversation_id: str,
        text: str,
    ) -> tuple[PrincipalConversation, str]:
        conversation = await self.conversation(
            db,
            principal=principal,
            provider=provider,
            external_conversation_id=external_conversation_id,
        )
        history = (
            await db.scalars(
                select(PrincipalMessage)
                .where(PrincipalMessage.conversation_id == conversation.id)
                .order_by(PrincipalMessage.created_at)
                .limit(40)
            )
        ).all()
        messages = [{"role": "system", "content": GUEST_SYSTEM_PROMPT}]
        messages.extend({"role": row.role, "content": row.content} for row in history)
        messages.append({"role": "user", "content": text[:12000]})
        assistant = await self.client.chat(messages, [])
        answer = str(assistant.get("content") or "I can help once your Operly account is linked.")[:16000]
        db.add(PrincipalMessage(conversation_id=conversation.id, role="user", content=text[:12000]))
        db.add(PrincipalMessage(conversation_id=conversation.id, role="assistant", content=answer))
        await db.flush()
        return conversation, answer


_GUEST_SERVICE: GuestConversationService | None = None


def get_guest_conversation_service() -> GuestConversationService:
    global _GUEST_SERVICE
    if _GUEST_SERVICE is None:
        _GUEST_SERVICE = GuestConversationService()
    return _GUEST_SERVICE
