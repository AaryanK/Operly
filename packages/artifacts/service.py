from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.artifact_models import ArtifactRecord

MAX_ARTIFACT_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArtifactScope:
    kind: str
    scope_id: str
    tenant_id: str | None = None
    owner_user_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "workspace":
            if not self.tenant_id or self.owner_user_id is not None:
                raise ValueError("Workspace artifacts require tenant_id and no owner_user_id")
        elif self.kind == "personal":
            if not self.owner_user_id or self.tenant_id is not None:
                raise ValueError("Personal artifacts require owner_user_id and no tenant_id")
        else:
            raise ValueError("Artifact scope must be workspace or personal")
        if not self.scope_id:
            raise ValueError("Artifact scope_id is required")


class ArtifactService:
    """Content-addressed durable artifact identity over the current database backend.

    Agent-visible and plugin-visible references remain Artifact IDs. The database row
    already carries ``storage_kind`` and ``storage_key`` so a future R2/S3 backend can
    replace byte persistence without changing callers or package manifests.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _scope_filter(scope: ArtifactScope):
        if scope.kind == "workspace":
            return (
                ArtifactRecord.scope_kind == "workspace",
                ArtifactRecord.scope_id == scope.scope_id,
                ArtifactRecord.tenant_id == scope.tenant_id,
                ArtifactRecord.owner_user_id.is_(None),
            )
        return (
            ArtifactRecord.scope_kind == "personal",
            ArtifactRecord.scope_id == scope.scope_id,
            ArtifactRecord.tenant_id.is_(None),
            ArtifactRecord.owner_user_id == scope.owner_user_id,
        )

    async def create_bytes(
        self,
        scope: ArtifactScope,
        *,
        filename: str,
        content: bytes,
        content_type: str | None = None,
        source: str = "platform",
        created_by: str | None = None,
        run_id: str | None = None,
        parent_artifact_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> ArtifactRecord:
        raw = bytes(content)
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError(f"Artifact exceeds {MAX_ARTIFACT_BYTES} byte database-backend limit")
        clean_name = str(filename or "artifact.bin").strip()[:255] or "artifact.bin"
        digest = hashlib.sha256(raw).hexdigest()
        row = ArtifactRecord(
            scope_kind=scope.kind,
            scope_id=scope.scope_id,
            tenant_id=scope.tenant_id,
            owner_user_id=scope.owner_user_id,
            run_id=run_id,
            parent_artifact_id=parent_artifact_id,
            created_by=created_by,
            filename=clean_name,
            content_type=str(content_type or "application/octet-stream")[:200],
            size_bytes=len(raw),
            sha256=digest,
            source=str(source or "platform")[:80],
            version=1,
            storage_kind="database",
            storage_key=None,
            content_bytes=raw,
            metadata_json=json.dumps(dict(metadata or {}), separators=(",", ":"), sort_keys=True),
            expires_at=expires_at,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def get(self, scope: ArtifactScope, artifact_id: str) -> ArtifactRecord:
        row = await self.db.scalar(
            select(ArtifactRecord).where(
                ArtifactRecord.id == artifact_id,
                *self._scope_filter(scope),
            )
        )
        if row is None:
            raise LookupError("Artifact not found in the authorized scope")
        return row

    async def read_bytes(self, scope: ArtifactScope, artifact_id: str) -> bytes:
        row = await self.get(scope, artifact_id)
        if row.storage_kind != "database":
            raise RuntimeError(f"Artifact storage backend is not configured: {row.storage_kind}")
        if row.content_bytes is None:
            raise RuntimeError("Artifact bytes are unavailable")
        raw = bytes(row.content_bytes)
        if hashlib.sha256(raw).hexdigest() != row.sha256:
            raise RuntimeError("Artifact digest verification failed")
        return raw

    async def list(
        self,
        scope: ArtifactScope,
        *,
        limit: int = 100,
        run_id: str | None = None,
    ) -> list[ArtifactRecord]:
        filters = list(self._scope_filter(scope))
        if run_id:
            filters.append(ArtifactRecord.run_id == run_id)
        rows = (
            await self.db.scalars(
                select(ArtifactRecord)
                .where(*filters)
                .order_by(ArtifactRecord.created_at.desc())
                .limit(max(1, min(int(limit), 200)))
            )
        ).all()
        return list(rows)

    async def assert_workspace_artifact(self, *, tenant_id: str, artifact_id: str) -> ArtifactRecord:
        return await self.get(
            ArtifactScope("workspace", tenant_id, tenant_id=tenant_id),
            artifact_id,
        )


def artifact_json(row: ArtifactRecord) -> dict[str, Any]:
    return {
        "artifact_id": row.id,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "source": row.source,
        "version": row.version,
        "storage_kind": row.storage_kind,
        "run_id": row.run_id,
        "parent_artifact_id": row.parent_artifact_id,
        "metadata": json.loads(row.metadata_json or "{}"),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }
