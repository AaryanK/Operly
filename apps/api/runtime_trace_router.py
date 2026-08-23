"""Authenticated runtime-trace reports for the current person's conversations."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.database.agent_models import AgentConversation
from packages.database.model_trace import conversation_trace_report
from packages.database.models import TenantMember
from packages.database.principal_models import Principal, PrincipalConversation

router = APIRouter(prefix="/api/runtime-traces", tags=["runtime-traces"])


@router.get("/conversations/{conversation_id}")
async def get_conversation_runtime_trace(
    conversation_id: str,
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Return model attempts, inputs, outputs, routing metadata, and failures.

    This endpoint is account-authenticated and verifies conversation ownership before
    returning trace payloads. Workspace traces additionally require a current
    membership, so leaving a workspace revokes access to its historical debug data.
    Provider credentials are never recorded; credential-shaped content is redacted.
    """
    user_id = account.user.id

    workspace_conversation = await db.get(AgentConversation, conversation_id)
    if (
        workspace_conversation is not None
        and workspace_conversation.principal_id == f"user:{user_id}"
    ):
        membership = await db.scalar(
            select(TenantMember).where(
                TenantMember.user_id == user_id,
                TenantMember.tenant_id == workspace_conversation.tenant_id,
            )
        )
        if membership is not None:
            report = await conversation_trace_report(
                db,
                conversation_id=workspace_conversation.id,
                user_id=user_id,
                tenant_id=workspace_conversation.tenant_id,
            )
            return {
                "scope": "workspace",
                "tenantId": workspace_conversation.tenant_id,
                **report,
            }

    principal = await db.scalar(
        select(Principal).where(
            Principal.kind == "human",
            Principal.user_id == user_id,
        )
    )
    if principal is not None:
        personal_conversation = await db.scalar(
            select(PrincipalConversation).where(
                PrincipalConversation.principal_id == principal.id,
                PrincipalConversation.provider == "operly_web",
                PrincipalConversation.external_conversation_id == conversation_id,
            )
        )
        if personal_conversation is not None:
            report = await conversation_trace_report(
                db,
                conversation_id=personal_conversation.external_conversation_id,
                user_id=user_id,
            )
            return {
                "scope": "personal",
                "principalConversationId": personal_conversation.id,
                **report,
            }

    raise HTTPException(status_code=404, detail="Conversation not found")
