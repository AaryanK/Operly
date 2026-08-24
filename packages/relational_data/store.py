"""Trusted relational application-data plane.

The store is deliberately separate from ``packages.database`` (Operly control-plane
metadata). Generated apps address logical tables only; the gateway maps every table
to an opaque workspace+application-prefixed physical table and never accepts raw SQL.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from packages.database.db import normalize_database_url
from packages.relational_data.contracts import (
    AddColumn,
    CreateIndex,
    CreateTable,
    DeleteRequest,
    FilterClause,
    InsertRequest,
    QueryRequest,
    RelationalMigration,
    UpdateRequest,
)


class RelationalDataError(ValueError):
    pass


_TYPE_SQL = {
    "string": "TEXT",
    "integer": "BIGINT",
    "number": "DOUBLE PRECISION",
    "boolean": "BOOLEAN",
    "datetime": "TIMESTAMP",
    "json": "TEXT",
    "uuid": "VARCHAR(36)",
}


def configured_app_data_url(explicit: str | None = None) -> str:
    if explicit:
        return normalize_database_url(explicit)
    configured = os.getenv("OPERLY_APP_DATA_DATABASE_URL", "").strip()
    environment = os.getenv("OPERLY_ENV", os.getenv("APP_ENV", "development")).lower()
    if not configured:
        if environment in {"production", "prod"}:
            raise RelationalDataError("OPERLY_APP_DATA_DATABASE_URL is not configured")
        configured = "sqlite+aiosqlite:///./operly-app-data.db"
    normalized = normalize_database_url(configured)
    control = os.getenv("DATABASE_URL", "").strip()
    if environment in {"production", "prod"} and control:
        if normalize_database_url(control) == normalized:
            raise RelationalDataError(
                "Application data must use a database separate from the Operly control-plane database"
            )
    return normalized


def _q(identifier: str) -> str:
    # Identifiers have already passed the strict lowercase contract. Quoting is a
    # second boundary so future reserved words cannot become SQL syntax.
    return '"' + identifier.replace('"', '""') + '"'


def _namespace(workspace_id: str, application_id: str) -> str:
    digest = hashlib.sha256(f"{workspace_id}\0{application_id}".encode()).hexdigest()[:20]
    return "app_" + digest


def _physical(workspace_id: str, application_id: str, logical_table: str) -> str:
    return f"{_namespace(workspace_id, application_id)}__{logical_table}"


def _checksum(migration: RelationalMigration) -> str:
    raw = json.dumps(migration.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def _column_json(column) -> dict[str, Any]:
    return column.model_dump(mode="json")


def _encode_value(column: dict[str, Any], value: Any) -> Any:
    if value is None:
        return None
    kind = column["type"]
    if kind == "json":
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if kind == "boolean":
        if not isinstance(value, bool):
            raise RelationalDataError(f"Column {column['name']} requires a boolean")
        return value
    if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise RelationalDataError(f"Column {column['name']} requires an integer")
    if kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise RelationalDataError(f"Column {column['name']} requires a number")
    if kind in {"string", "uuid", "datetime"} and not isinstance(value, str):
        raise RelationalDataError(f"Column {column['name']} requires a string value")
    return value


def _decode_value(column: dict[str, Any], value: Any) -> Any:
    if value is None:
        return None
    if column["type"] == "json" and isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


class RelationalDataStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = configured_app_data_url(database_url)
        self.engine: AsyncEngine = create_async_engine(self.database_url, pool_pre_ping=True)
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS operly_app_data_migrations (
                        workspace_id VARCHAR(120) NOT NULL,
                        application_id VARCHAR(160) NOT NULL,
                        version BIGINT NOT NULL,
                        name VARCHAR(120) NOT NULL,
                        checksum VARCHAR(80) NOT NULL,
                        applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (workspace_id, application_id, version)
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS operly_app_data_tables (
                        workspace_id VARCHAR(120) NOT NULL,
                        application_id VARCHAR(160) NOT NULL,
                        logical_name VARCHAR(63) NOT NULL,
                        physical_name VARCHAR(100) NOT NULL,
                        columns_json TEXT NOT NULL,
                        PRIMARY KEY (workspace_id, application_id, logical_name),
                        UNIQUE (physical_name)
                    )
                    """
                )
            )
        self._initialized = True

    async def close(self) -> None:
        await self.engine.dispose()

    async def _catalog(self, connection, workspace_id: str, application_id: str, table: str):
        row = (
            await connection.execute(
                text(
                    "SELECT physical_name, columns_json FROM operly_app_data_tables "
                    "WHERE workspace_id=:workspace AND application_id=:application AND logical_name=:table"
                ),
                {"workspace": workspace_id, "application": application_id, "table": table},
            )
        ).mappings().first()
        if row is None:
            raise RelationalDataError(f"Unknown application table: {table}")
        try:
            columns = json.loads(row["columns_json"])
        except json.JSONDecodeError as error:
            raise RelationalDataError("Stored application table contract is invalid") from error
        return str(row["physical_name"]), {item["name"]: item for item in columns}

    async def apply_migrations(
        self,
        workspace_id: str,
        application_id: str,
        migrations: Iterable[RelationalMigration],
    ) -> dict[str, Any]:
        await self.initialize()
        ordered = sorted(migrations, key=lambda item: item.version)
        if len({item.version for item in ordered}) != len(ordered):
            raise RelationalDataError("Migration versions must be unique")
        applied_now: list[int] = []
        async with self.engine.begin() as connection:
            existing_rows = (
                await connection.execute(
                    text(
                        "SELECT version, checksum FROM operly_app_data_migrations "
                        "WHERE workspace_id=:workspace AND application_id=:application ORDER BY version"
                    ),
                    {"workspace": workspace_id, "application": application_id},
                )
            ).mappings().all()
            existing = {int(row["version"]): str(row["checksum"]) for row in existing_rows}
            current = max(existing, default=0)
            for migration in ordered:
                checksum = _checksum(migration)
                if migration.version in existing:
                    if existing[migration.version] != checksum:
                        raise RelationalDataError(
                            f"Migration {migration.version} checksum changed after it was applied"
                        )
                    continue
                if migration.version != current + 1:
                    raise RelationalDataError(
                        f"Migration sequence must be contiguous; expected version {current + 1}"
                    )
                for operation in migration.operations:
                    await self._apply_operation(connection, workspace_id, application_id, operation)
                await connection.execute(
                    text(
                        "INSERT INTO operly_app_data_migrations "
                        "(workspace_id, application_id, version, name, checksum) "
                        "VALUES (:workspace, :application, :version, :name, :checksum)"
                    ),
                    {
                        "workspace": workspace_id,
                        "application": application_id,
                        "version": migration.version,
                        "name": migration.name,
                        "checksum": checksum,
                    },
                )
                existing[migration.version] = checksum
                current = migration.version
                applied_now.append(migration.version)
        return {"currentVersion": current, "appliedVersions": applied_now}

    async def _apply_operation(self, connection, workspace_id: str, application_id: str, operation) -> None:
        if isinstance(operation, CreateTable):
            await self._create_table(connection, workspace_id, application_id, operation)
            return
        if isinstance(operation, AddColumn):
            await self._add_column(connection, workspace_id, application_id, operation)
            return
        if isinstance(operation, CreateIndex):
            await self._create_index(connection, workspace_id, application_id, operation)
            return
        raise RelationalDataError("Unsupported relational migration operation")

    async def _create_table(self, connection, workspace_id: str, application_id: str, operation: CreateTable) -> None:
        physical = _physical(workspace_id, application_id, operation.table)
        definitions: list[str] = []
        for column in operation.columns:
            clause = f"{_q(column.name)} {_TYPE_SQL[column.type]}"
            if column.primaryKey:
                clause += " PRIMARY KEY"
            if not column.nullable:
                clause += " NOT NULL"
            if column.unique:
                clause += " UNIQUE"
            if column.references:
                target = _physical(workspace_id, application_id, column.references.table)
                clause += f" REFERENCES {_q(target)}({_q(column.references.column)})"
            definitions.append(clause)
        await connection.execute(text(f"CREATE TABLE {_q(physical)} ({', '.join(definitions)})"))
        await connection.execute(
            text(
                "INSERT INTO operly_app_data_tables "
                "(workspace_id, application_id, logical_name, physical_name, columns_json) "
                "VALUES (:workspace, :application, :logical, :physical, :columns)"
            ),
            {
                "workspace": workspace_id,
                "application": application_id,
                "logical": operation.table,
                "physical": physical,
                "columns": json.dumps([_column_json(item) for item in operation.columns], sort_keys=True),
            },
        )

    async def _add_column(self, connection, workspace_id: str, application_id: str, operation: AddColumn) -> None:
        physical, columns = await self._catalog(connection, workspace_id, application_id, operation.table)
        if operation.column.name in columns:
            raise RelationalDataError(f"Column already exists: {operation.column.name}")
        column = operation.column
        clause = f"{_q(column.name)} {_TYPE_SQL[column.type]}"
        if not column.nullable:
            raise RelationalDataError("relational v1 add_column requires nullable=true for portable migrations")
        if column.unique or column.references:
            raise RelationalDataError("relational v1 add_column does not add unique/reference constraints")
        await connection.execute(text(f"ALTER TABLE {_q(physical)} ADD COLUMN {clause}"))
        updated = list(columns.values()) + [_column_json(column)]
        await connection.execute(
            text(
                "UPDATE operly_app_data_tables SET columns_json=:columns "
                "WHERE workspace_id=:workspace AND application_id=:application AND logical_name=:logical"
            ),
            {
                "columns": json.dumps(updated, sort_keys=True),
                "workspace": workspace_id,
                "application": application_id,
                "logical": operation.table,
            },
        )

    async def _create_index(self, connection, workspace_id: str, application_id: str, operation: CreateIndex) -> None:
        physical, columns = await self._catalog(connection, workspace_id, application_id, operation.table)
        missing = [column for column in operation.columns if column not in columns]
        if missing:
            raise RelationalDataError(f"Index references unknown columns: {', '.join(missing)}")
        physical_index = f"{_namespace(workspace_id, application_id)}__idx__{operation.name}"
        unique = "UNIQUE " if operation.unique else ""
        column_sql = ", ".join(_q(column) for column in operation.columns)
        await connection.execute(
            text(f"CREATE {unique}INDEX {_q(physical_index)} ON {_q(physical)} ({column_sql})")
        )

    @staticmethod
    def _validate_columns(columns: dict[str, dict], requested: Iterable[str]) -> None:
        missing = [name for name in requested if name not in columns]
        if missing:
            raise RelationalDataError(f"Unknown columns: {', '.join(sorted(set(missing)))}")

    def _where(self, columns: dict[str, dict], filters: Iterable[FilterClause]) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        operators = {"eq": "=", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
        for index, clause in enumerate(filters):
            self._validate_columns(columns, (clause.column,))
            column = columns[clause.column]
            if clause.op == "is_null":
                clauses.append(f"{_q(clause.column)} IS {'NOT ' if clause.value is False else ''}NULL")
                continue
            if clause.op == "in":
                values = clause.value or []
                if not values:
                    clauses.append("1=0")
                    continue
                keys = []
                for item_index, value in enumerate(values):
                    key = f"f_{index}_{item_index}"
                    keys.append(":" + key)
                    params[key] = _encode_value(column, value)
                clauses.append(f"{_q(clause.column)} IN ({', '.join(keys)})")
                continue
            key = f"f_{index}"
            params[key] = _encode_value(column, clause.value)
            clauses.append(f"{_q(clause.column)} {operators[clause.op]} :{key}")
        return (" AND ".join(clauses) if clauses else "1=1"), params

    async def query(self, workspace_id: str, application_id: str, request: QueryRequest) -> dict[str, Any]:
        await self.initialize()
        async with self.engine.connect() as connection:
            physical, columns = await self._catalog(connection, workspace_id, application_id, request.table)
            selected = list(request.columns) or list(columns)
            self._validate_columns(columns, selected)
            self._validate_columns(columns, (item.column for item in request.orderBy))
            where, params = self._where(columns, request.filters)
            order = ""
            if request.orderBy:
                order = " ORDER BY " + ", ".join(
                    f"{_q(item.column)} {item.direction.upper()}" for item in request.orderBy
                )
            params.update({"limit": request.limit, "offset": request.offset})
            sql = (
                f"SELECT {', '.join(_q(item) for item in selected)} FROM {_q(physical)} "
                f"WHERE {where}{order} LIMIT :limit OFFSET :offset"
            )
            rows = (await connection.execute(text(sql), params)).mappings().all()
            return {
                "rows": [
                    {name: _decode_value(columns[name], row[name]) for name in selected}
                    for row in rows
                ],
                "count": len(rows),
            }

    async def insert(self, workspace_id: str, application_id: str, request: InsertRequest) -> dict[str, Any]:
        await self.initialize()
        async with self.engine.begin() as connection:
            physical, columns = await self._catalog(connection, workspace_id, application_id, request.table)
            self._validate_columns(columns, request.values)
            names = list(request.values)
            values = {name: _encode_value(columns[name], request.values[name]) for name in names}
            sql = (
                f"INSERT INTO {_q(physical)} ({', '.join(_q(name) for name in names)}) "
                f"VALUES ({', '.join(':' + name for name in names)})"
            )
            result = await connection.execute(text(sql), values)
            return {"inserted": 1 if result.rowcount in {-1, None} else int(result.rowcount), "values": request.values}

    async def update(self, workspace_id: str, application_id: str, request: UpdateRequest) -> dict[str, Any]:
        await self.initialize()
        async with self.engine.begin() as connection:
            physical, columns = await self._catalog(connection, workspace_id, application_id, request.table)
            self._validate_columns(columns, request.values)
            params = {
                f"v_{name}": _encode_value(columns[name], value)
                for name, value in request.values.items()
            }
            assignments = ", ".join(f"{_q(name)}=:v_{name}" for name in request.values)
            where, filter_params = self._where(columns, request.filters)
            params.update(filter_params)
            result = await connection.execute(
                text(f"UPDATE {_q(physical)} SET {assignments} WHERE {where}"), params
            )
            return {"updated": max(0, int(result.rowcount or 0))}

    async def delete(self, workspace_id: str, application_id: str, request: DeleteRequest) -> dict[str, Any]:
        await self.initialize()
        async with self.engine.begin() as connection:
            physical, columns = await self._catalog(connection, workspace_id, application_id, request.table)
            where, params = self._where(columns, request.filters)
            result = await connection.execute(text(f"DELETE FROM {_q(physical)} WHERE {where}"), params)
            return {"deleted": max(0, int(result.rowcount or 0))}


__all__ = [
    "RelationalDataError",
    "RelationalDataStore",
    "configured_app_data_url",
]
