from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.connectors.secrets import store_secret
from packages.plugins.webhooks import WebhookVerificationError, webhooks

management_router = APIRouter(prefix="/api/plugin-platform/webhooks", tags=["plugin-platform"])
public_router = APIRouter(prefix="/api/public/webhooks", tags=["webhooks"])


class CreateWebhookInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: str = Field(min_length=1, max_length=160)
    installation_id: str | None = Field(default=None, max_length=80)
    verification_type: str = Field(default="none", pattern="^(none|hmac_sha256)$")
    webhook_secret: str | None = Field(default=None, max_length=4096)
    max_body_bytes: int = Field(default=1024 * 1024, ge=1024, le=1024 * 1024)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _owner(auth: AuthContext) -> None:
    if auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only Workspace owners can manage webhook ingress")


@management_router.post("", status_code=201)
async def create_webhook_endpoint(
    payload: CreateWebhookInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    secret_reference = None
    if payload.verification_type == "hmac_sha256":
        if not payload.webhook_secret:
            raise HTTPException(status_code=422, detail="webhook_secret is required for HMAC verification")
        secret_reference = await store_secret(
            db,
            auth.tenant.id,
            {
                "purpose": "plugin_webhook_hmac",
                "webhook_secret": payload.webhook_secret,
            },
        )
    try:
        created = await webhooks.create_endpoint(
            db,
            tenant_id=auth.tenant.id,
            event_type=payload.event_type,
            installation_id=payload.installation_id,
            verification_type=payload.verification_type,
            secret_reference=secret_reference,
            max_body_bytes=payload.max_body_bytes,
            created_by=auth.user.id,
            metadata=payload.metadata,
        )
        await db.commit()
    except (LookupError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "endpoint_id": created.endpoint_id,
        "endpoint_key": created.endpoint_key,
        "event_type": created.event_type,
        "path": f"/api/public/webhooks/{created.endpoint_key}",
        "secret_returned": False,
        "note": "The endpoint key is shown once. Rotate by creating a replacement endpoint.",
    }


@public_router.post("/{endpoint_key}")
async def receive_webhook(
    endpoint_key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        body = await request.body()
        receipt = await webhooks.receive(
            db,
            endpoint_key=endpoint_key,
            body=body,
            headers=request.headers,
        )
        await db.commit()
        return {
            "accepted": True,
            "receipt_id": receipt.id,
            "duplicate": receipt.processing_state != "accepted",
        }
    except WebhookVerificationError as error:
        await db.rollback()
        raise HTTPException(status_code=401, detail=str(error)) from error
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=413, detail=str(error)) from error
