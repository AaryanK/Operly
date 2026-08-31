from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.connector_models import TenantConnector


def connector_scopes(connector: TenantConnector) -> set[str]:
    try:
        raw = json.loads(connector.granted_scopes_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()} if isinstance(raw, list) else set()


def connector_configuration(connector: TenantConnector) -> dict[str, Any]:
    try:
        raw = json.loads(connector.configuration_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


async def active_workspace_connectors(
    db: AsyncSession,
    workspace_id: str,
    provider: str,
) -> list[TenantConnector]:
    rows = (
        await db.scalars(
            select(TenantConnector).where(
                TenantConnector.tenant_id == workspace_id,
                TenantConnector.provider == provider,
                TenantConnector.enabled.is_(True),
                TenantConnector.status == "connected",
            )
        )
    ).all()
    return list(rows)


def connector_public_json(connector: TenantConnector) -> dict[str, Any]:
    config = connector_configuration(connector)
    return {
        "id": connector.id,
        "provider": connector.provider,
        "connector_type": connector.connector_type,
        "display_name": connector.display_name,
        "account": config.get("display_name") or connector.provider_account_id,
        "status": connector.status,
        "enabled": bool(connector.enabled),
        "health_status": connector.health_status,
        "last_health_check": (
            connector.last_health_check.isoformat() if connector.last_health_check else None
        ),
        "last_error": connector.last_error,
        "scopes": sorted(connector_scopes(connector)),
        "configuration": {
            key: value
            for key, value in config.items()
            if key in {"display_name", "team_id", "calendar_id"}
        },
    }
