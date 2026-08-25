import json
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.database.models import SecurityEvent


router = APIRouter(prefix="/api/analytics", tags=["analytics"])
_EVENT_TYPE = "product_page_view"
_DEFAULT_COUNTRY_HEADERS = (
    "cf-ipcountry",
    "x-vercel-ip-country",
    "cloudfront-viewer-country",
)


def _country_code(request: Request) -> str | None:
    configured = os.getenv("OPERLY_COUNTRY_HEADER", "").strip().lower()
    candidates = ((configured,) if configured else ()) + _DEFAULT_COUNTRY_HEADERS
    for header in candidates:
        value = str(request.headers.get(header) or "").strip().upper()
        if len(value) == 2 and value.isalpha() and value not in {"XX"}:
            return value
    return None


def _clean_path(value: object) -> str | None:
    path = str(value or "").strip()
    if not path or not path.startswith("/"):
        return None
    path = path.split("?", 1)[0].split("#", 1)[0]
    return path[:500]


def _metadata(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@router.post("/event", status_code=202)
async def record_product_event(
    payload: dict,
    request: Request,
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    if str(payload.get("event_name") or "").strip().lower() != "page_view":
        return {"ok": True, "recorded": False}

    path = _clean_path(payload.get("path"))
    now = datetime.utcnow()
    recent_events = (
        await db.scalars(
            select(SecurityEvent)
            .where(
                SecurityEvent.user_id == account.user.id,
                SecurityEvent.event_type == _EVENT_TYPE,
                SecurityEvent.outcome == "succeeded",
                SecurityEvent.created_at >= now - timedelta(seconds=20),
            )
            .order_by(SecurityEvent.created_at.desc())
            .limit(8)
        )
    ).all()
    for recent in recent_events:
        metadata = _metadata(recent.metadata_json)
        if metadata.get("session_id") == account.session.id and metadata.get("path") == path:
            return {"ok": True, "recorded": False}

    metadata = {
        "path": path,
        "session_id": account.session.id,
    }
    country_code = _country_code(request)
    if country_code:
        metadata["country_code"] = country_code

    db.add(
        SecurityEvent(
            user_id=account.user.id,
            tenant_id=account.session.tenant_id,
            event_type=_EVENT_TYPE,
            outcome="succeeded",
            ip_hash=None,
            metadata_json=json.dumps(metadata, separators=(",", ":"), sort_keys=True),
            created_at=now,
        )
    )
    await db.commit()
    return {"ok": True, "recorded": True}
