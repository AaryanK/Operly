from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import or_, select

from packages.database.artifact_models import ArtifactRecord


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


MAX_ARTIFACT_BYTES = _env_int("OPERLY_MAX_ARTIFACT_MB", 100, 1, 500) * 1024 * 1024
MAX_BATCH_ARTIFACTS = _env_int("OPERLY_MAX_BATCH_ARTIFACTS", 500, 1, 1000)


@dataclass(frozen=True, slots=True)
class ArtifactScope:
    kind: str
    scope_id: str
    tenant_id: str | None = None
    owner_user_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"workspace", "personal"}:
            raise ValueError("Artifact scope must be workspace or personal")
        if not self.scope_id:
            raise ValueError("Artifact scope_id is required")
        if self.kind == "workspace":
            if not self.tenant_id or self.owner_user_id:
                raise ValueError("Workspace artifacts require tenant_id only")
        elif not self.owner_user_id or self.tenant_id:
            raise ValueError("Personal artifacts require owner_user_id only")


def artifact_scope_from_context(context) -> ArtifactScope:
    kind = str(getattr(context, "scope_kind", "workspace") or "workspace")
    scope_id = str(getattr(context, "scope_id", "") or "")
    tenant_id = str(getattr(context, "tenant_id", "") or "") or None
    owner_user_id = str(getattr(context, "owner_user_id", "") or "") or None
    if kind == "personal":
        owner_user_id = owner_user_id or str(getattr(context, "actor_id", "") or "") or None
        scope_id = scope_id or (f"personal:{owner_user_id}" if owner_user_id else "")
        return ArtifactScope("personal", scope_id, owner_user_id=owner_user_id)
    scope_id = scope_id or tenant_id or ""
    return ArtifactScope("workspace", scope_id, tenant_id=tenant_id)


def _safe_filename(value: str) -> str:
    name = Path(str(value or "artifact.bin")).name.replace("\x00", "")
    name = _SAFE_NAME.sub("-", name).strip(" .")[:255]
    return name or "artifact.bin"


def artifact_json(row: ArtifactRecord, *, include_metadata: bool = True) -> dict[str, Any]:
    try:
        metadata = json.loads(row.metadata_json or "{}") if include_metadata else {}
    except (TypeError, json.JSONDecodeError):
        metadata = {}
    return {
        "artifact_id": row.id,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "source": row.source,
        "run_id": row.run_id,
        "parent_artifact_id": row.parent_artifact_id,
        "version": row.version,
        "storage_kind": row.storage_kind,
        "metadata": metadata,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


class ArtifactService:
    """Scope-enforced artifact storage used by AI, Studio, workflows and connectors."""

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _scope_predicate(scope: ArtifactScope):
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
            ArtifactRecord.owner_user_id == scope.owner_user_id,
            ArtifactRecord.tenant_id.is_(None),
        )

    async def create_bytes(
        self,
        scope: ArtifactScope,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
        source: str = "agent",
        created_by: str | None = None,
        run_id: str | None = None,
        parent_artifact_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> ArtifactRecord:
        raw = bytes(content)
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError(f"Artifact exceeds {MAX_ARTIFACT_BYTES} byte limit")
        digest = hashlib.sha256(raw).hexdigest()
        row = ArtifactRecord(
            scope_kind=scope.kind,
            scope_id=scope.scope_id,
            tenant_id=scope.tenant_id,
            owner_user_id=scope.owner_user_id,
            run_id=(str(run_id)[:120] if run_id else None),
            parent_artifact_id=(str(parent_artifact_id)[:36] if parent_artifact_id else None),
            created_by=(str(created_by)[:120] if created_by else None),
            filename=_safe_filename(filename),
            content_type=str(content_type or "application/octet-stream")[:200],
            size_bytes=len(raw),
            sha256=digest,
            source=str(source or "agent")[:80],
            storage_kind="database",
            content_bytes=raw,
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True, default=str)[:100_000],
            expires_at=expires_at,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def create_path(
        self,
        scope: ArtifactScope,
        path: str | Path,
        *,
        filename: str | None = None,
        content_type: str | None = None,
        source: str = "agent",
        created_by: str | None = None,
        run_id: str | None = None,
        parent_artifact_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        source_path = Path(path)
        raw = source_path.read_bytes()
        return await self.create_bytes(
            scope,
            filename=filename or source_path.name,
            content_type=content_type,
            content=raw,
            source=source,
            created_by=created_by,
            run_id=run_id,
            parent_artifact_id=parent_artifact_id,
            metadata=metadata,
        )

    async def get(self, scope: ArtifactScope, artifact_id: str) -> ArtifactRecord:
        row = await self.db.scalar(
            select(ArtifactRecord).where(
                ArtifactRecord.id == str(artifact_id),
                *self._scope_predicate(scope),
                or_(ArtifactRecord.expires_at.is_(None), ArtifactRecord.expires_at > datetime.utcnow()),
            )
        )
        if row is None:
            raise LookupError("Artifact not found in the current execution scope")
        return row

    async def get_many(
        self,
        scope: ArtifactScope,
        artifact_ids: Iterable[str],
        *,
        max_items: int = MAX_BATCH_ARTIFACTS,
    ) -> list[ArtifactRecord]:
        ids = [str(item) for item in artifact_ids if str(item).strip()]
        if not ids:
            return []
        if len(ids) > max_items:
            raise ValueError(f"Maximum {max_items} artifacts per operation")
        if len(ids) != len(set(ids)):
            raise ValueError("Artifact IDs must be unique")
        rows = list(
            (
                await self.db.scalars(
                    select(ArtifactRecord).where(
                        ArtifactRecord.id.in_(ids),
                        *self._scope_predicate(scope),
                        or_(ArtifactRecord.expires_at.is_(None), ArtifactRecord.expires_at > datetime.utcnow()),
                    )
                )
            ).all()
        )
        by_id = {row.id: row for row in rows}
        missing = [item for item in ids if item not in by_id]
        if missing:
            raise LookupError("One or more artifacts are unavailable in the current execution scope")
        return [by_id[item] for item in ids]

    async def list(
        self,
        scope: ArtifactScope,
        *,
        limit: int = 50,
        run_id: str | None = None,
        content_type_prefix: str | None = None,
    ) -> list[ArtifactRecord]:
        clauses = [
            *self._scope_predicate(scope),
            or_(ArtifactRecord.expires_at.is_(None), ArtifactRecord.expires_at > datetime.utcnow()),
        ]
        if run_id:
            clauses.append(ArtifactRecord.run_id == str(run_id))
        if content_type_prefix:
            clauses.append(ArtifactRecord.content_type.like(f"{str(content_type_prefix)[:100]}%"))
        return list(
            (
                await self.db.scalars(
                    select(ArtifactRecord)
                    .where(*clauses)
                    .order_by(ArtifactRecord.created_at.desc())
                    .limit(max(1, min(int(limit), 200)))
                )
            ).all()
        )

    async def read_bytes(self, scope: ArtifactScope, artifact_id: str) -> bytes:
        row = await self.get(scope, artifact_id)
        if row.storage_kind != "database" or row.content_bytes is None:
            raise RuntimeError("Artifact storage backend is not readable by this runtime")
        raw = bytes(row.content_bytes)
        if hashlib.sha256(raw).hexdigest() != row.sha256:
            raise RuntimeError("Artifact integrity check failed")
        return raw

    async def materialize(
        self,
        scope: ArtifactScope,
        artifact_id: str,
        directory: str | Path,
        *,
        filename: str | None = None,
    ) -> Path:
        row = await self.get(scope, artifact_id)
        raw = await self.read_bytes(scope, artifact_id)
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        target = root / _safe_filename(filename or row.filename)
        target.write_bytes(raw)
        return target
