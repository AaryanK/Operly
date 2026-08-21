from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.workspace_security_models import WorkspaceRole, WorkspaceRolePermission


DEFAULT_ROLE_AUTHORITY: dict[str, set[str]] = {
    "owner": {
        "company:read", "research:read", "analytics:read", "crm:read", "crm:write",
        "website:read", "website:write", "messaging:draft", "messaging:curate",
        "messaging:read", "messaging:write", "messaging:send", "gmail:read",
        "gmail:write", "gmail:draft", "calendar:read", "calendar:write",
        "solution:read", "solution:generate", "solution:write", "tasks:read",
        "tasks:write", "memory:read", "memory:write", "messages:read",
        "catalog:write", "inventory:write", "orders:write", "quotes:write",
        "operations:read", "operations:write", "reminders:write",
        "discord:read", "discord:write",
        "context:human:read", "context:human:write", "context:tenant:read",
        "context:tenant:write", "context:conversation:read", "context:conversation:write",
        "workspace:read", "workspace:members:manage", "workspace:roles:manage",
        "workspace:channels:manage", "workspace:clients:manage", "workspace:tools:expose",
    },
    "manager": {
        "company:read", "research:read", "analytics:read", "crm:read", "crm:write",
        "website:read", "website:write", "messaging:draft", "messaging:curate",
        "messaging:read", "messaging:write", "messaging:send", "gmail:read",
        "gmail:write", "gmail:draft", "calendar:read", "calendar:write",
        "solution:read", "tasks:read", "tasks:write", "memory:read", "memory:write",
        "messages:read", "catalog:write", "inventory:write", "orders:write",
        "quotes:write", "operations:read", "operations:write", "reminders:write",
        "discord:read", "discord:write",
        "context:human:read", "context:human:write", "context:tenant:read",
        "context:tenant:write", "context:conversation:read", "context:conversation:write",
        "workspace:read",
    },
    "agent": {
        "company:read", "research:read", "analytics:read", "crm:read", "crm:write",
        "website:read", "messaging:draft", "messaging:curate", "messaging:read",
        "gmail:read", "gmail:draft", "calendar:read", "solution:read", "tasks:read",
        "tasks:write", "memory:read", "memory:write", "messages:read", "operations:read",
        "reminders:write", "discord:read", "discord:write",
        "context:human:read", "context:human:write",
        "context:tenant:read", "context:tenant:write", "context:conversation:read",
        "context:conversation:write", "workspace:read",
    },
    "employee": {
        "company:read", "analytics:read", "crm:read", "website:read", "solution:read",
        "tasks:read", "messages:read", "memory:read", "messaging:read", "discord:read",
        "context:human:read", "context:human:write", "context:tenant:read",
        "context:conversation:read", "context:conversation:write", "workspace:read",
    },
}


KNOWN_PERMISSIONS = frozenset(
    permission for permissions in DEFAULT_ROLE_AUTHORITY.values() for permission in permissions
)


def default_permissions(role: str) -> set[str]:
    return set(DEFAULT_ROLE_AUTHORITY.get(str(role or "").strip().lower(), set()))


async def resolve_workspace_permissions(db: AsyncSession, *, tenant_id: str, role: str) -> set[str]:
    role_key = str(role or "").strip().lower()
    if not role_key:
        return set()
    workspace_role = await db.scalar(
        select(WorkspaceRole).where(
            WorkspaceRole.tenant_id == tenant_id,
            WorkspaceRole.key == role_key,
        )
    )
    if workspace_role is None:
        return default_permissions(role_key)
    rows = (
        await db.scalars(
            select(WorkspaceRolePermission.permission).where(
                WorkspaceRolePermission.role_id == workspace_role.id
            )
        )
    ).all()
    return {str(permission) for permission in rows if permission in KNOWN_PERMISSIONS}


def normalize_role_key(value: str) -> str:
    raw = "-".join(str(value or "").strip().lower().split())
    cleaned = "".join(
        character for character in raw if character.isalnum() or character in {"-", "_"}
    )
    if not cleaned:
        raise ValueError("Role key is required")
    return cleaned[:30]


def validate_permissions(values: list[str] | set[str]) -> set[str]:
    permissions = {str(value).strip() for value in values if str(value).strip()}
    unknown = sorted(permissions - KNOWN_PERMISSIONS)
    if unknown:
        raise ValueError("Unknown permissions: " + ", ".join(unknown))
    return permissions
