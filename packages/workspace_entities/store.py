"""Workspace-scoped canonical entity store backed by the application-data database."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from packages.relational_data.store import configured_app_data_url
from packages.workspace_entities.contracts import CANONICAL_ENTITY_SCHEMAS, EntityCreate, EntityList, EntityUpdate


class WorkspaceEntityError(ValueError):
    pass


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _namespace(workspace_id: str) -> str:
    return "ws_" + hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:20]


def _physical(workspace_id: str, kind: str) -> str:
    return f"{_namespace(workspace_id)}__{kind}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _schema(kind: str) -> dict[str, Any]:
    try:
        return CANONICAL_ENTITY_SCHEMAS[kind]
    except KeyError as error:
        raise WorkspaceEntityError(f"Unsupported canonical entity kind: {kind}") from error


def _validate_values(kind: str, values: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    schema = _schema(kind)["fields"]
    unknown = sorted(set(values) - set(schema))
    if unknown:
        raise WorkspaceEntityError(f"Unknown {kind} fields: {', '.join(unknown)}")
    if not creating and "id" in values:
        raise WorkspaceEntityError("Canonical entity id is immutable")
    normalized = dict(values)
    if creating:
        normalized.setdefault("id", str(uuid.uuid4()))
        normalized.setdefault("status", "active")
        required = [name for name, spec in schema.items() if spec.get("required") and "default" not in spec]
        missing = [name for name in required if normalized.get(name) in {None, ""}]
        if missing:
            raise WorkspaceEntityError(f"Missing required {kind} fields: {', '.join(missing)}")
    for name, value in normalized.items():
        expected = schema[name]["type"]
        if value is None:
            continue
        if expected == "json":
            normalized[name] = json.dumps(value, sort_keys=True, separators=(",", ":"))
        elif expected in {"string", "uuid"} and not isinstance(value, str):
            raise WorkspaceEntityError(f"{kind}.{name} requires a string")
    return normalized


class WorkspaceEntityStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = configured_app_data_url(database_url)
        self.engine: AsyncEngine = create_async_engine(self.database_url, future=True)
        self._initialized: set[str] = set()

    async def close(self) -> None:
        await self.engine.dispose()

    async def initialize(self, workspace_id: str) -> None:
        if workspace_id in self._initialized:
            return
        async with self.engine.begin() as conn:
            for kind in ("location", "employee", "customer"):
                table = _physical(workspace_id, kind)
                if kind == "location":
                    sql = f"""
                    CREATE TABLE IF NOT EXISTS {_q(table)} (
                      id VARCHAR(120) PRIMARY KEY,
                      name TEXT NOT NULL,
                      code TEXT,
                      timezone TEXT,
                      status TEXT NOT NULL,
                      metadata TEXT,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    )
                    """
                else:
                    sql = f"""
                    CREATE TABLE IF NOT EXISTS {_q(table)} (
                      id VARCHAR(120) PRIMARY KEY,
                      display_name TEXT NOT NULL,
                      email TEXT,
                      phone TEXT,
                      status TEXT NOT NULL,
                      location_id VARCHAR(120),
                      metadata TEXT,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    )
                    """
                await conn.execute(text(sql))
        self._initialized.add(workspace_id)

    async def schema(self) -> dict[str, Any]:
        return {"schemaVersion": "operly.workspace-entities/v1", "entities": CANONICAL_ENTITY_SCHEMAS}

    async def create(self, workspace_id: str, request: EntityCreate) -> dict[str, Any]:
        await self.initialize(workspace_id)
        values = _validate_values(request.kind, request.values, creating=True)
        now = _now()
        values.update({"created_at": now, "updated_at": now})
        columns = list(values)
        params = {f"v{i}": values[name] for i, name in enumerate(columns)}
        sql = f"INSERT INTO {_q(_physical(workspace_id, request.kind))} ({', '.join(_q(x) for x in columns)}) VALUES ({', '.join(':' + f'v{i}' for i in range(len(columns)))})"
        try:
            async with self.engine.begin() as conn:
                await conn.execute(text(sql), params)
        except Exception as error:
            raise WorkspaceEntityError("Canonical entity could not be created") from error
        return await self.get(workspace_id, request.kind, str(values["id"]))

    async def get(self, workspace_id: str, kind: str, entity_id: str) -> dict[str, Any]:
        await self.initialize(workspace_id)
        async with self.engine.connect() as conn:
            row = (await conn.execute(text(f"SELECT * FROM {_q(_physical(workspace_id, kind))} WHERE id=:id"), {"id": entity_id})).mappings().first()
        if row is None:
            raise WorkspaceEntityError(f"{kind} entity not found")
        return self._decode(dict(row))

    async def list(self, workspace_id: str, request: EntityList) -> dict[str, Any]:
        await self.initialize(workspace_id)
        where: list[str] = []
        params: dict[str, Any] = {"limit": request.limit, "offset": request.offset}
        if request.status is not None:
            where.append("status=:status"); params["status"] = request.status
        if request.locationId is not None and request.kind != "location":
            where.append("location_id=:location_id"); params["location_id"] = request.locationId
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        sql = f"SELECT * FROM {_q(_physical(workspace_id, request.kind))}{clause} ORDER BY created_at, id LIMIT :limit OFFSET :offset"
        async with self.engine.connect() as conn:
            rows = (await conn.execute(text(sql), params)).mappings().all()
        return {"kind": request.kind, "rows": [self._decode(dict(row)) for row in rows]}

    async def update(self, workspace_id: str, request: EntityUpdate) -> dict[str, Any]:
        await self.initialize(workspace_id)
        values = _validate_values(request.kind, request.values, creating=False)
        values["updated_at"] = _now()
        assignments = []
        params: dict[str, Any] = {"id": request.entityId}
        for i, (name, value) in enumerate(values.items()):
            key = f"v{i}"; assignments.append(f"{_q(name)}=:{key}"); params[key] = value
        async with self.engine.begin() as conn:
            result = await conn.execute(text(f"UPDATE {_q(_physical(workspace_id, request.kind))} SET {', '.join(assignments)} WHERE id=:id"), params)
            if result.rowcount != 1:
                raise WorkspaceEntityError(f"{request.kind} entity not found")
        return await self.get(workspace_id, request.kind, request.entityId)

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        if row.get("metadata") is not None and isinstance(row["metadata"], str):
            try: row["metadata"] = json.loads(row["metadata"])
            except json.JSONDecodeError: row["metadata"] = None
        return row


__all__ = ["WorkspaceEntityError", "WorkspaceEntityStore"]
