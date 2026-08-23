from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from apps.api.dependencies import AccountAuthContext, get_account_auth_context
from packages.business_brain.personal_agent import get_personal_agent_service


router = APIRouter(prefix="/api/personal-agent", tags=["personal-agent"])


class PersonalChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=12_000)
    conversation_id: str | None = Field(default=None, max_length=255)
    selected_workspace_id: str | None = Field(default=None, max_length=36)


@router.post("/chat")
async def chat(
    payload: PersonalChatInput,
    auth: AccountAuthContext = Depends(get_account_auth_context),
):
    try:
        return await get_personal_agent_service().run(
            user_id=auth.user.id,
            display_name=auth.user.display_name,
            message=payload.message,
            conversation_id=payload.conversation_id,
            selected_workspace_id=payload.selected_workspace_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/conversations")
async def conversations(
    auth: AccountAuthContext = Depends(get_account_auth_context),
):
    return await get_personal_agent_service().list_conversations(
        user_id=auth.user.id,
        display_name=auth.user.display_name,
    )


@router.get("/conversations/{conversation_id}/messages")
async def messages(
    conversation_id: str,
    auth: AccountAuthContext = Depends(get_account_auth_context),
):
    try:
        return await get_personal_agent_service().messages(
            user_id=auth.user.id,
            conversation_id=conversation_id,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
