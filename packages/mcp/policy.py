import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.principal_models import ClientGrant, WorkspaceToolExposure


class McpPolicyError(ValueError):
    pass


async def active_client_scopes(
    db: AsyncSession,
    *,
    principal_id: str,
    client_id: str,
    tenant_id: str | None,
) -> set[str]:
    statement = select(ClientGrant).where(
        ClientGrant.principal_id == principal_id,
        ClientGrant.client_id == client_id,
        ClientGrant.status == "active",
    )
    rows = (await db.scalars(statement)).all()
    now = datetime.utcnow()
    scopes: set[str] = set()
    for row in rows:
        if row.expires_at and row.expires_at <= now:
            continue
        if row.tenant_id is not None and row.tenant_id != tenant_id:
            continue
        try:
            scopes.update(str(item) for item in json.loads(row.scopes_json or "[]"))
        except (TypeError, json.JSONDecodeError):
            continue
    return scopes


async def exposed_tools(
    db: AsyncSession,
    *,
    tenant_id: str,
    surface: str = "mcp",
    authenticated: bool,
) -> dict[str, str]:
    rows = (
        await db.scalars(
            select(WorkspaceToolExposure).where(
                WorkspaceToolExposure.tenant_id == tenant_id,
                WorkspaceToolExposure.surface == surface,
                WorkspaceToolExposure.exposed.is_(True),
            )
        )
    ).all()
    result: dict[str, str] = {}
    for row in rows:
        if row.access_mode == "public" or authenticated:
            result[row.tool_id] = row.access_mode
    return result


def effective_tool_access(
    *,
    tool_id: str,
    principal_permissions: set[str],
    required_permissions: set[str],
    client_scopes: set[str],
    exposed: bool,
    public: bool = False,
) -> bool:
    if not exposed:
        return False
    if public:
        return required_permissions.issubset(principal_permissions) if required_permissions else True
    if not required_permissions.issubset(principal_permissions):
        return False
    # Client grants may contain exact tool IDs, permission scopes, or wildcard read grants.
    return (
        tool_id in client_scopes
        or required_permissions.issubset(client_scopes)
        or "*" in client_scopes
    )
