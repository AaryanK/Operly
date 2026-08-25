import json
import os
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.admin_ai_usage import build_admin_ai_usage
from apps.api.dependencies import AccountAuthContext, get_account_auth_context, get_db
from packages.database.models import (
    AppUser,
    AuthSession,
    SecurityEvent,
    Tenant,
    TenantMember,
)


router = APIRouter(prefix="/api/admin", tags=["platform-admin"])
LOGIN_EVENT_TYPES = ("login_success", "google_authentication_success")
PRODUCT_PAGE_VIEW = "product_page_view"
PRODUCT_ACTIVITY_TYPES = (PRODUCT_PAGE_VIEW, "product_heartbeat")


def _configured_admin_email() -> str:
    return os.getenv("ADMIN_EMAIL", "").strip().lower()


async def require_platform_admin(
    account: AccountAuthContext = Depends(get_account_auth_context),
) -> AccountAuthContext:
    admin_email = _configured_admin_email()
    if not admin_email:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ADMIN_NOT_CONFIGURED",
                "message": "Platform admin access is not configured.",
            },
        )
    if account.user.email.strip().lower() != admin_email:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PLATFORM_ADMIN_REQUIRED",
                "message": "This account is not authorized for the platform admin console.",
            },
        )
    return account


def _event_metadata(event: SecurityEvent) -> dict:
    try:
        parsed = json.loads(event.metadata_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _event_country(event: SecurityEvent) -> str | None:
    value = str(_event_metadata(event).get("country_code") or "").strip().upper()
    if len(value) == 2 and value.isalpha() and value != "XX":
        return value
    return None


async def _active_user_count(
    db: AsyncSession,
    cutoff: datetime,
    *,
    admin_email: str,
) -> int:
    value = await db.scalar(
        select(func.count(func.distinct(SecurityEvent.user_id)))
        .join(AppUser, AppUser.id == SecurityEvent.user_id)
        .where(
            SecurityEvent.created_at >= cutoff,
            SecurityEvent.outcome == "succeeded",
            SecurityEvent.event_type.in_(PRODUCT_ACTIVITY_TYPES),
            func.lower(AppUser.email) != admin_email,
        )
    )
    return int(value or 0)


async def _geography_summary(
    db: AsyncSession,
    *,
    since: datetime,
    admin_user_id: str,
) -> dict:
    events = (
        await db.scalars(
            select(SecurityEvent)
            .where(
                SecurityEvent.created_at >= since,
                SecurityEvent.outcome == "succeeded",
                SecurityEvent.event_type == PRODUCT_PAGE_VIEW,
                SecurityEvent.user_id.is_not(None),
                SecurityEvent.user_id != admin_user_id,
            )
            .order_by(SecurityEvent.created_at.desc())
        )
    ).all()

    buckets: dict[str, dict] = defaultdict(lambda: {"visits": 0, "users": set()})
    unknown_visits = 0
    located_visits = 0
    path_counts: dict[str, int] = defaultdict(int)
    for event in events:
        metadata = _event_metadata(event)
        path = str(metadata.get("path") or "").strip()
        if path:
            path_counts[path] += 1
        code = _event_country(event)
        if not code:
            unknown_visits += 1
            continue
        located_visits += 1
        buckets[code]["visits"] += 1
        if event.user_id:
            buckets[code]["users"].add(event.user_id)

    countries = [
        {
            "country_code": code,
            "visits": values["visits"],
            "unique_users": len(values["users"]),
        }
        for code, values in buckets.items()
    ]
    countries.sort(key=lambda item: (item["unique_users"], item["visits"]), reverse=True)
    total = located_visits + unknown_visits
    top_paths = [
        {"path": path, "views": views}
        for path, views in sorted(path_counts.items(), key=lambda item: item[1], reverse=True)[:10]
    ]
    return {
        "countries": countries,
        "located_views": located_visits,
        "unknown_views": unknown_visits,
        "total_views": total,
        "coverage_percent": round((located_visits / total) * 100, 1) if total else 0.0,
        "top_paths": top_paths,
    }


def _day_key(value: datetime) -> str:
    return value.date().isoformat()


@router.get("/session")
async def admin_session(
    account: AccountAuthContext = Depends(require_platform_admin),
):
    return {
        "ok": True,
        "user": {
            "id": account.user.id,
            "email": account.user.email,
            "display_name": account.user.display_name,
        },
    }


@router.get("/overview")
async def admin_overview(
    account: AccountAuthContext = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.utcnow()
    admin_email = _configured_admin_email()
    customer_filter = func.lower(AppUser.email) != admin_email

    total_users = int(
        await db.scalar(select(func.count(AppUser.id)).where(customer_filter)) or 0
    )
    active_accounts = int(
        await db.scalar(
            select(func.count(AppUser.id)).where(customer_filter, AppUser.active.is_(True))
        )
        or 0
    )
    verified_users = int(
        await db.scalar(
            select(func.count(AppUser.id)).where(
                customer_filter,
                AppUser.email_verified_at.is_not(None),
            )
        )
        or 0
    )
    total_workspaces = int(await db.scalar(select(func.count(Tenant.id))) or 0)
    total_memberships = int(await db.scalar(select(func.count(TenantMember.id))) or 0)
    signups_today = int(
        await db.scalar(
            select(func.count(AppUser.id)).where(
                customer_filter,
                AppUser.created_at >= datetime.combine(now.date(), datetime.min.time()),
            )
        )
        or 0
    )

    dau = await _active_user_count(db, now - timedelta(days=1), admin_email=admin_email)
    wau = await _active_user_count(db, now - timedelta(days=7), admin_email=admin_email)
    mau = await _active_user_count(db, now - timedelta(days=30), admin_email=admin_email)
    active_now = await _active_user_count(db, now - timedelta(minutes=15), admin_email=admin_email)

    period_start = datetime.combine((now - timedelta(days=29)).date(), datetime.min.time())
    signup_rows = (
        await db.scalars(
            select(AppUser.created_at).where(
                customer_filter,
                AppUser.created_at >= period_start,
            )
        )
    ).all()
    signin_rows = (
        await db.scalars(
            select(SecurityEvent.created_at).where(
                SecurityEvent.created_at >= period_start,
                SecurityEvent.outcome == "succeeded",
                SecurityEvent.event_type.in_(LOGIN_EVENT_TYPES),
                SecurityEvent.user_id.is_not(None),
                SecurityEvent.user_id != account.user.id,
            )
        )
    ).all()

    signup_by_day: dict[str, int] = defaultdict(int)
    signin_by_day: dict[str, int] = defaultdict(int)
    for created_at in signup_rows:
        signup_by_day[_day_key(created_at)] += 1
    for created_at in signin_rows:
        signin_by_day[_day_key(created_at)] += 1

    activity = []
    for offset in range(30):
        day = (period_start + timedelta(days=offset)).date().isoformat()
        activity.append(
            {
                "date": day,
                "signups": signup_by_day.get(day, 0),
                "signins": signin_by_day.get(day, 0),
            }
        )

    geography = await _geography_summary(
        db,
        since=now - timedelta(days=30),
        admin_user_id=account.user.id,
    )

    recent_users = (
        await db.scalars(
            select(AppUser)
            .where(customer_filter)
            .order_by(AppUser.created_at.desc())
            .limit(8)
        )
    ).all()

    return {
        "generated_at": now,
        "metrics": {
            "users": total_users,
            "active_accounts": active_accounts,
            "verified_users": verified_users,
            "workspaces": total_workspaces,
            "memberships": total_memberships,
            "signups_today": signups_today,
            "active_now": active_now,
            "dau": dau,
            "wau": wau,
            "mau": mau,
        },
        "activity": activity,
        "geography": geography,
        "recent_users": [
            {
                "id": user.id,
                "display_name": user.display_name,
                "email": user.email,
                "active": user.active,
                "verified": user.email_verified_at is not None,
                "created_at": user.created_at,
            }
            for user in recent_users
        ],
    }


@router.get("/users")
async def admin_users(
    limit: int = Query(default=100, ge=1, le=500),
    _: AccountAuthContext = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    users = (
        await db.scalars(select(AppUser).order_by(AppUser.created_at.desc()).limit(limit))
    ).all()
    if not users:
        return []

    user_ids = [user.id for user in users]
    memberships = (
        await db.execute(
            select(TenantMember.user_id, TenantMember.tenant_id, TenantMember.role).where(
                TenantMember.user_id.in_(user_ids)
            )
        )
    ).all()
    tenant_ids = {row.tenant_id for row in memberships}
    tenants = (
        await db.scalars(select(Tenant).where(Tenant.id.in_(tenant_ids)))
    ).all() if tenant_ids else []
    tenant_names = {tenant.id: tenant.name for tenant in tenants}

    workspace_map: dict[str, list[dict]] = defaultdict(list)
    for membership in memberships:
        workspace_map[membership.user_id].append(
            {
                "id": membership.tenant_id,
                "name": tenant_names.get(membership.tenant_id, "Unknown workspace"),
                "role": membership.role,
            }
        )

    sessions = (
        await db.scalars(
            select(AuthSession)
            .where(AuthSession.user_id.in_(user_ids))
            .order_by(AuthSession.last_activity_at.desc())
        )
    ).all()
    latest_session: dict[str, AuthSession] = {}
    for session in sessions:
        latest_session.setdefault(session.user_id, session)

    latest_event_subquery = (
        select(
            SecurityEvent.user_id.label("user_id"),
            func.max(SecurityEvent.created_at).label("created_at"),
        )
        .where(
            SecurityEvent.user_id.in_(user_ids),
            SecurityEvent.outcome == "succeeded",
            SecurityEvent.event_type.in_(PRODUCT_ACTIVITY_TYPES),
        )
        .group_by(SecurityEvent.user_id)
        .subquery()
    )
    location_events = (
        await db.scalars(
            select(SecurityEvent).join(
                latest_event_subquery,
                and_(
                    SecurityEvent.user_id == latest_event_subquery.c.user_id,
                    SecurityEvent.created_at == latest_event_subquery.c.created_at,
                ),
            )
        )
    ).all()
    latest_country: dict[str, str | None] = {}
    for event in location_events:
        if event.user_id and event.user_id not in latest_country:
            latest_country[event.user_id] = _event_country(event)

    admin_email = _configured_admin_email()
    return [
        {
            "id": user.id,
            "display_name": user.display_name,
            "email": user.email,
            "active": user.active,
            "verified": user.email_verified_at is not None,
            "is_admin": user.email.strip().lower() == admin_email,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "last_active_at": latest_session.get(user.id).last_activity_at if user.id in latest_session else None,
            "country_code": latest_country.get(user.id),
            "workspaces": sorted(workspace_map.get(user.id, []), key=lambda item: item["name"].lower()),
        }
        for user in users
    ]


@router.get("/workspaces")
async def admin_workspaces(
    limit: int = Query(default=100, ge=1, le=500),
    _: AccountAuthContext = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    tenants = (
        await db.scalars(select(Tenant).order_by(Tenant.created_at.desc()).limit(limit))
    ).all()
    if not tenants:
        return []

    tenant_ids = [tenant.id for tenant in tenants]
    memberships = (
        await db.execute(
            select(TenantMember.tenant_id, TenantMember.user_id, TenantMember.role).where(
                TenantMember.tenant_id.in_(tenant_ids)
            )
        )
    ).all()
    user_ids = {row.user_id for row in memberships}
    users = (
        await db.scalars(select(AppUser).where(AppUser.id.in_(user_ids)))
    ).all() if user_ids else []
    user_map = {user.id: user for user in users}

    member_map: dict[str, list[dict]] = defaultdict(list)
    for membership in memberships:
        user = user_map.get(membership.user_id)
        member_map[membership.tenant_id].append(
            {
                "user_id": membership.user_id,
                "display_name": user.display_name if user else "Unknown user",
                "email": user.email if user else None,
                "role": membership.role,
            }
        )

    return [
        {
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "timezone": tenant.timezone,
            "created_at": tenant.created_at,
            "member_count": len(member_map.get(tenant.id, [])),
            "members": sorted(
                member_map.get(tenant.id, []),
                key=lambda item: (item["role"] != "owner", item["display_name"].lower()),
            ),
        }
        for tenant in tenants
    ]


@router.get("/geography")
async def admin_geography(
    days: int = Query(default=30, ge=1, le=365),
    account: AccountAuthContext = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.utcnow()
    summary = await _geography_summary(
        db,
        since=now - timedelta(days=days),
        admin_user_id=account.user.id,
    )
    return {"days": days, **summary}


@router.get("/ai-usage")
async def admin_ai_usage(
    range_name: str = Query(default="24h", alias="range"),
    _: AccountAuthContext = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    return await build_admin_ai_usage(db, range_name)
