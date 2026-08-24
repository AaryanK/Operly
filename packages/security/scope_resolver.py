from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import AppUser, Tenant, TenantMember


class ResolvedScopeKind(StrEnum):
    PERSONAL = "personal"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class AuthorizedScope:
    kind: ResolvedScopeKind
    id: str
    name: str
    role: str | None = None
    slug: str | None = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "slug": self.slug,
        }


@dataclass(frozen=True, slots=True)
class ScopeResolution:
    status: str
    reference: str
    matches: tuple[AuthorizedScope, ...]

    @property
    def resolved(self) -> AuthorizedScope | None:
        return self.matches[0] if self.status == "resolved" and len(self.matches) == 1 else None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "reference": self.reference,
            "scope": self.resolved.as_dict() if self.resolved else None,
            "matches": [item.as_dict() for item in self.matches],
        }


_PERSONAL_ALIASES = {
    "personal",
    "my personal",
    "my account",
    "account",
    "personal account",
    "my personal space",
    "personal space",
}


def _normalize(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


async def authorized_scopes(db: AsyncSession, *, user_id: str) -> tuple[AuthorizedScope, ...]:
    """Return the scopes the current authenticated human may resolve.

    This inventory grants nothing by itself. Every later workspace execution must
    reacquire and revalidate a workspace ExecutionContext; Personal execution must
    reacquire a Personal ExecutionContext.
    """

    user = await db.get(AppUser, user_id)
    if user is None or not user.active:
        return ()

    memberships = (
        await db.execute(
            select(TenantMember, Tenant)
            .join(Tenant, Tenant.id == TenantMember.tenant_id)
            .where(TenantMember.user_id == user_id)
            .order_by(Tenant.name, Tenant.id)
        )
    ).all()
    return (
        AuthorizedScope(
            kind=ResolvedScopeKind.PERSONAL,
            id=f"personal:{user.id}",
            name="Personal",
            role="personal_owner",
            slug=None,
        ),
        *(
            AuthorizedScope(
                kind=ResolvedScopeKind.WORKSPACE,
                id=tenant.id,
                name=tenant.name,
                role=membership.role,
                slug=tenant.slug,
            )
            for membership, tenant in memberships
        ),
    )


async def resolve_authorized_scope(
    db: AsyncSession,
    *,
    user_id: str,
    reference: str,
    focus_workspace_id: str | None = None,
) -> ScopeResolution:
    """Resolve an explicit scope reference only within the user's authorized universe.

    Resolution is deliberately deterministic: exact IDs/slugs/names win, then a
    unique workspace-name containment match is allowed. The current workspace focus
    is used only for otherwise generic workspace references; it never changes the
    Personal scope or grants membership.
    """

    scopes = await authorized_scopes(db, user_id=user_id)
    needle = _normalize(reference)
    personal = tuple(item for item in scopes if item.kind is ResolvedScopeKind.PERSONAL)
    workspaces = tuple(item for item in scopes if item.kind is ResolvedScopeKind.WORKSPACE)

    if not scopes:
        return ScopeResolution("missing", needle, ())
    if needle in _PERSONAL_ALIASES or needle.startswith("personal:"):
        return ScopeResolution("resolved", needle, personal)

    exact = tuple(
        item
        for item in workspaces
        if needle
        and (
            needle == _normalize(item.id)
            or needle == _normalize(item.slug or "")
            or needle == _normalize(item.name)
        )
    )
    if len(exact) == 1:
        return ScopeResolution("resolved", needle, exact)
    if len(exact) > 1:
        return ScopeResolution("ambiguous", needle, exact)

    if needle in {"workspace", "my workspace", "current workspace", "focused workspace"}:
        focused = tuple(item for item in workspaces if item.id == focus_workspace_id)
        if len(focused) == 1:
            return ScopeResolution("resolved", needle, focused)
        return ScopeResolution("ambiguous" if len(workspaces) > 1 else "resolved" if len(workspaces) == 1 else "missing", needle, workspaces)

    contains = tuple(
        item for item in workspaces if needle and needle in _normalize(item.name)
    )
    if len(contains) == 1:
        return ScopeResolution("resolved", needle, contains)
    if len(contains) > 1:
        return ScopeResolution("ambiguous", needle, contains)
    return ScopeResolution("missing", needle, ())
