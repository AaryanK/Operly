import json
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.database.analytics_models import ProductAnalyticsEvent


router = APIRouter(prefix="/api/analytics", tags=["analytics"])
_ALLOWED_EVENTS = {"page_view", "heartbeat"}
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


@router.post("/event", status_code=202)
async def record_product_event(
    payload: dict,
    request: Request,
    account: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    event_name = str(payload.get("event_name") or "").strip().lower()
    if event_name not in _ALLOWED_EVENTS:
        return {"ok": True, "recorded": False}

    path = _clean_path(payload.get("path"))
    now = datetime.utcnow()
    dedupe_window = timedelta(seconds=20 if event_name == "page_view" else 240)
    recent = await db.scalar(
        select(ProductAnalyticsEvent.id)
        .where(
            ProductAnalyticsEvent.user_id == account.user.id,
            ProductAnalyticsEvent.session_id == account.session.id,
            ProductAnalyticsEvent.event_name == event_name,
            ProductAnalyticsEvent.path == path,
            ProductAnalyticsEvent.created_at >= now - dedupe_window,
        )
        .limit(1)
    )
    if recent:
        return {"ok": True, "recorded": False}

    event = ProductAnalyticsEvent(
        user_id=account.user.id,
        tenant_id=account.session.tenant_id,
        session_id=account.session.id,
        event_name=event_name,
        path=path,
        country_code=_country_code(request),
        metadata_json=json.dumps({}, separators=(",", ":")),
        created_at=now,
    )
    db.add(event)
    await db.commit()
    return {"ok": True, "recorded": True}
