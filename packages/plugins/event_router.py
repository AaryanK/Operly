from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.connectors.secrets import store_secret
from packages.database.plugin_platform_models import (
    DigitalEventSubscriptionRecord,
    PluginInstallationRecord,
    PluginVersionRecord,
)
from packages.plugins.contracts import PluginContractError, PluginManifest


router = APIRouter(prefix="/api/plugin-platform", tags=["plugin-platform-events"])

_EVENT_PATTERN = re.compile(r"^(?:\*|[a-z][a-z0-9_.-]{1,177}(?:\.\*)?)$")


class EventSubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_pattern: str = Field(min_length=1, max_length=180)
    target_url: str = Field(min_length=8, max_length=240)
    signing_secret: str | None = Field(default=None, min_length=16, max_length=4096)
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)
    max_attempts: int = Field(default=8, ge=1, le=50)
    max_response_bytes: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)


def _owner(auth: AuthContext) -> None:
    if auth.role != "owner":
        raise HTTPException(
            status_code=403,
            detail="Only Workspace owners can manage plugin event subscriptions",
        )


def _pattern_valid(value: str) -> bool:
    clean = str(value or "").strip().lower()
    if not _EVENT_PATTERN.fullmatch(clean):
        return False
    if ".." in clean or clean.startswith(".") or clean.endswith("."):
        return False
    return True


def _declared_pattern_allows(declared: str, requested: str) -> bool:
    declared = declared.strip().lower()
    requested = requested.strip().lower()
    if declared == "*":
        return requested != "*" or requested == "*"
    if declared.endswith(".*"):
        prefix = declared[:-1]
        return requested == declared or requested.startswith(prefix)
    return declared == requested


def _validate_target_url(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError as error:
        raise ValueError("Event subscription target URL is invalid") from error
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Event subscription target must be an HTTPS URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Event subscription target may not include credentials or fragments")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".localhost", ".local", ".internal", ".home.arpa")
    ):
        raise ValueError("Event subscription target may not use a local hostname")
    return raw


async def _installation_manifest(
    db: AsyncSession,
    *,
    tenant_id: str,
    installation_id: str,
) -> tuple[PluginInstallationRecord, PluginManifest]:
    installation = await db.scalar(
        select(PluginInstallationRecord).where(
            PluginInstallationRecord.id == installation_id,
            PluginInstallationRecord.tenant_id == tenant_id,
        )
    )
    if installation is None:
        raise LookupError("Plugin installation not found")
    version = await db.get(PluginVersionRecord, installation.version_id)
    if version is None:
        raise LookupError("Plugin version not found")
    try:
        manifest = PluginManifest.from_dict(json.loads(version.manifest_json))
    except (PluginContractError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError("Installed plugin manifest is invalid") from error
    return installation, manifest


@router.get("/installations/{installation_id}/event-subscriptions")
async def list_event_subscriptions(
    installation_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    await _installation_manifest(
        db, tenant_id=auth.tenant.id, installation_id=installation_id
    )
    rows = list(
        (
            await db.scalars(
                select(DigitalEventSubscriptionRecord)
                .where(
                    DigitalEventSubscriptionRecord.tenant_id == auth.tenant.id,
                    DigitalEventSubscriptionRecord.installation_id == installation_id,
                )
                .order_by(DigitalEventSubscriptionRecord.created_at.asc())
            )
        ).all()
    )
    return {
        "subscriptions": [
            {
                "id": row.id,
                "event_pattern": row.event_pattern,
                "target_kind": row.target_kind,
                "target_reference": row.target_reference,
                "enabled": row.enabled,
                "signed": bool(row.secret_reference),
                "delivery_policy": json.loads(row.delivery_policy_json or "{}"),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    }


@router.post("/installations/{installation_id}/event-subscriptions", status_code=201)
async def create_event_subscription(
    installation_id: str,
    payload: EventSubscriptionInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    try:
        _, manifest = await _installation_manifest(
            db, tenant_id=auth.tenant.id, installation_id=installation_id
        )
        event_pattern = payload.event_pattern.strip().lower()
        if not _pattern_valid(event_pattern):
            raise ValueError("Event subscription pattern is invalid")
        declared = [str(item).strip().lower() for item in manifest.consumes_events]
        if not any(_declared_pattern_allows(item, event_pattern) for item in declared):
            raise PermissionError(
                "Plugin manifest did not declare authority to consume this event pattern"
            )
        target = _validate_target_url(payload.target_url)
        existing = await db.scalar(
            select(DigitalEventSubscriptionRecord).where(
                DigitalEventSubscriptionRecord.tenant_id == auth.tenant.id,
                DigitalEventSubscriptionRecord.installation_id == installation_id,
                DigitalEventSubscriptionRecord.event_pattern == event_pattern,
                DigitalEventSubscriptionRecord.target_kind == "webhook",
                DigitalEventSubscriptionRecord.target_reference == target,
            )
        )
        if existing is not None:
            raise ValueError("This plugin event subscription already exists")

        secret_reference = None
        if payload.signing_secret:
            secret_reference = await store_secret(
                db,
                auth.tenant.id,
                {
                    "purpose": "plugin_event_delivery_hmac",
                    "installation_id": installation_id,
                    "webhook_secret": payload.signing_secret,
                },
            )
        row = DigitalEventSubscriptionRecord(
            tenant_id=auth.tenant.id,
            installation_id=installation_id,
            event_pattern=event_pattern,
            target_kind="webhook",
            target_reference=target,
            secret_reference=secret_reference,
            enabled=True,
            delivery_policy_json=json.dumps(
                {
                    "timeout_seconds": payload.timeout_seconds,
                    "max_attempts": payload.max_attempts,
                    "max_response_bytes": payload.max_response_bytes,
                    "redirects": "disabled",
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            created_by=auth.user.id,
        )
        db.add(row)
        await db.flush()
        await db.commit()
        return {
            "id": row.id,
            "event_pattern": row.event_pattern,
            "target_kind": row.target_kind,
            "target_reference": row.target_reference,
            "enabled": row.enabled,
            "signed": bool(row.secret_reference),
        }
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Encrypted Workspace event-signing secret storage is unavailable",
        ) from error


@router.delete("/installations/{installation_id}/event-subscriptions/{subscription_id}")
async def disable_event_subscription(
    installation_id: str,
    subscription_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _owner(auth)
    await _installation_manifest(
        db, tenant_id=auth.tenant.id, installation_id=installation_id
    )
    row = await db.scalar(
        select(DigitalEventSubscriptionRecord).where(
            DigitalEventSubscriptionRecord.id == subscription_id,
            DigitalEventSubscriptionRecord.tenant_id == auth.tenant.id,
            DigitalEventSubscriptionRecord.installation_id == installation_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Event subscription not found")
    row.enabled = False
    await db.commit()
    return {"disabled": True, "id": row.id}
