from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.security import hash_token, normalize_email, random_token
from packages.database.identity_graph_models import WorkspaceInvitation
from packages.database.models import AppUser, Tenant, TenantMember


INVITE_TTL_DAYS = 7


class WorkspaceInvitationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorkspaceInvitationView:
    invitation_id: str
    workspace_id: str
    workspace_name: str
    role: str
    targeted: bool
    target_email: str | None
    expires_at: datetime
    status: str

    def as_dict(self, *, reveal_email: bool = False) -> dict:
        return {
            "invitation_id": self.invitation_id,
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "role": self.role,
            "targeted": self.targeted,
            "target_email": self.target_email if reveal_email else None,
            "expires_at": self.expires_at.isoformat(),
            "status": self.status,
        }


class WorkspaceInvitationService:
    @staticmethod
    def _token_hash(token: str) -> str:
        return hash_token(token, purpose="workspace-invitation")

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        *,
        tenant_id: str,
        role: str,
        invited_by_user_id: str | None,
        target_email: str | None = None,
        source: str = "operly_web",
        metadata: dict | None = None,
        ttl_days: int = INVITE_TTL_DAYS,
    ) -> tuple[WorkspaceInvitation, str]:
        workspace = await db.get(Tenant, tenant_id)
        if workspace is None:
            raise WorkspaceInvitationError("Workspace is unavailable")
        email = normalize_email(target_email) if target_email else None
        token = random_token()
        row = WorkspaceInvitation(
            tenant_id=tenant_id,
            role=str(role).strip().lower(),
            target_email=email,
            token_hash=cls._token_hash(token),
            status="pending",
            invited_by_user_id=invited_by_user_id,
            source=str(source or "operly_web")[:60],
            metadata_json=json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True),
            expires_at=datetime.utcnow() + timedelta(days=max(1, min(int(ttl_days), 30))),
        )
        db.add(row)
        await db.flush()
        return row, token

    @classmethod
    async def _pending_by_token(
        cls,
        db: AsyncSession,
        token: str,
    ) -> WorkspaceInvitation | None:
        clean = str(token or "").strip()
        if len(clean) < 20:
            return None
        return await db.scalar(
            select(WorkspaceInvitation).where(
                WorkspaceInvitation.token_hash == cls._token_hash(clean),
                WorkspaceInvitation.status == "pending",
                WorkspaceInvitation.expires_at > datetime.utcnow(),
            )
        )

    @classmethod
    async def inspect(
        cls,
        db: AsyncSession,
        *,
        token: str,
    ) -> WorkspaceInvitationView | None:
        row = await cls._pending_by_token(db, token)
        if row is None:
            return None
        workspace = await db.get(Tenant, row.tenant_id)
        if workspace is None:
            return None
        return WorkspaceInvitationView(
            invitation_id=row.id,
            workspace_id=workspace.id,
            workspace_name=workspace.name,
            role=row.role,
            targeted=bool(row.target_email),
            target_email=row.target_email,
            expires_at=row.expires_at,
            status=row.status,
        )

    @classmethod
    async def accept(
        cls,
        db: AsyncSession,
        *,
        token: str,
        user_id: str,
    ) -> TenantMember:
        row = await cls._pending_by_token(db, token)
        if row is None:
            raise WorkspaceInvitationError("Workspace invitation is invalid or expired")
        user = await db.get(AppUser, user_id)
        if user is None or not user.active:
            raise WorkspaceInvitationError("Operly user is unavailable")
        if row.target_email and normalize_email(user.email) != row.target_email:
            raise WorkspaceInvitationError(
                "This workspace invitation was issued to a different email address"
            )

        membership = await db.scalar(
            select(TenantMember).where(
                TenantMember.tenant_id == row.tenant_id,
                TenantMember.user_id == user.id,
            )
        )
        if membership is None:
            membership = TenantMember(
                tenant_id=row.tenant_id,
                user_id=user.id,
                role=row.role,
            )
            db.add(membership)
            await db.flush()

        row.status = "accepted"
        row.accepted_by_user_id = user.id
        row.accepted_at = datetime.utcnow()
        await db.flush()
        return membership

    @staticmethod
    async def list_for_workspace(
        db: AsyncSession,
        *,
        tenant_id: str,
        limit: int = 100,
    ) -> list[WorkspaceInvitation]:
        return list(
            (
                await db.scalars(
                    select(WorkspaceInvitation)
                    .where(WorkspaceInvitation.tenant_id == tenant_id)
                    .order_by(WorkspaceInvitation.created_at.desc())
                    .limit(max(1, min(int(limit), 250)))
                )
            ).all()
        )

    @staticmethod
    async def revoke(
        db: AsyncSession,
        *,
        tenant_id: str,
        invitation_id: str,
    ) -> WorkspaceInvitation:
        row = await db.scalar(
            select(WorkspaceInvitation).where(
                WorkspaceInvitation.id == invitation_id,
                WorkspaceInvitation.tenant_id == tenant_id,
            )
        )
        if row is None:
            raise WorkspaceInvitationError("Workspace invitation not found")
        if row.status == "pending":
            row.status = "revoked"
        await db.flush()
        return row
