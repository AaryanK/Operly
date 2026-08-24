"""Provider-neutral contracts for Operly-managed relational application data.

Generated software never receives a database URL or provider credential. It declares
migrations and talks to a scoped capability endpoint using these logical contracts.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RELATIONAL_CAPABILITY_ID = "data.relational"
RELATIONAL_MIGRATION_SCHEMA = "operly.relational.migration/v1"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _identifier(value: str) -> str:
    value = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("Relational identifiers must be lowercase snake_case and at most 63 characters")
    return value


class ColumnReference(StrictModel):
    table: str
    column: str

    @field_validator("table", "column")
    @classmethod
    def safe_identifier(cls, value: str) -> str:
        return _identifier(value)


class ColumnDefinition(StrictModel):
    name: str
    type: Literal["string", "integer", "number", "boolean", "datetime", "json", "uuid"]
    nullable: bool = True
    primaryKey: bool = False
    unique: bool = False
    references: ColumnReference | None = None

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        return _identifier(value)

    @model_validator(mode="after")
    def primary_key_not_nullable(self):
        if self.primaryKey and self.nullable:
            object.__setattr__(self, "nullable", False)
        return self


class CreateTable(StrictModel):
    op: Literal["create_table"] = "create_table"
    table: str
    columns: tuple[ColumnDefinition, ...] = Field(min_length=1, max_length=100)

    @field_validator("table")
    @classmethod
    def safe_table(cls, value: str) -> str:
        return _identifier(value)

    @model_validator(mode="after")
    def validate_columns(self):
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("Migration table columns must be unique")
        if sum(1 for column in self.columns if column.primaryKey) > 1:
            raise ValueError("Relational v1 supports one primary-key column per table")
        return self


class AddColumn(StrictModel):
    op: Literal["add_column"] = "add_column"
    table: str
    column: ColumnDefinition

    @field_validator("table")
    @classmethod
    def safe_table(cls, value: str) -> str:
        return _identifier(value)

    @model_validator(mode="after")
    def no_primary_key_add(self):
        if self.column.primaryKey:
            raise ValueError("A primary key cannot be added after table creation in relational v1")
        return self


class CreateIndex(StrictModel):
    op: Literal["create_index"] = "create_index"
    table: str
    name: str
    columns: tuple[str, ...] = Field(min_length=1, max_length=16)
    unique: bool = False

    @field_validator("table", "name")
    @classmethod
    def safe_identifier(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("columns")
    @classmethod
    def safe_columns(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_identifier(value) for value in values)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Index columns must be unique")
        return normalized


MigrationOperation = CreateTable | AddColumn | CreateIndex


class RelationalMigration(StrictModel):
    schemaVersion: Literal["operly.relational.migration/v1"] = RELATIONAL_MIGRATION_SCHEMA
    version: int = Field(ge=1, le=100000)
    name: str = Field(min_length=1, max_length=120)
    operations: tuple[MigrationOperation, ...] = Field(min_length=1, max_length=100)


class FilterClause(StrictModel):
    column: str
    op: Literal["eq", "ne", "lt", "lte", "gt", "gte", "in", "is_null"] = "eq"
    value: Any = None

    @field_validator("column")
    @classmethod
    def safe_column(cls, value: str) -> str:
        return _identifier(value)

    @model_validator(mode="after")
    def validate_value(self):
        if self.op == "in" and (not isinstance(self.value, list) or len(self.value) > 100):
            raise ValueError("in filters require a list of at most 100 values")
        if self.op == "is_null" and self.value not in {None, True, False}:
            raise ValueError("is_null accepts only true, false, or null")
        return self


class OrderClause(StrictModel):
    column: str
    direction: Literal["asc", "desc"] = "asc"

    @field_validator("column")
    @classmethod
    def safe_column(cls, value: str) -> str:
        return _identifier(value)


class QueryRequest(StrictModel):
    table: str
    columns: tuple[str, ...] = ()
    filters: tuple[FilterClause, ...] = ()
    orderBy: tuple[OrderClause, ...] = ()
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=1_000_000)

    @field_validator("table")
    @classmethod
    def safe_table(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("columns")
    @classmethod
    def safe_columns(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_identifier(value) for value in values)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Selected columns must be unique")
        return normalized


class InsertRequest(StrictModel):
    table: str
    values: dict[str, Any] = Field(min_length=1, max_length=100)

    @field_validator("table")
    @classmethod
    def safe_table(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("values")
    @classmethod
    def safe_values(cls, values: dict[str, Any]) -> dict[str, Any]:
        return {_identifier(key): value for key, value in values.items()}


class UpdateRequest(StrictModel):
    table: str
    values: dict[str, Any] = Field(min_length=1, max_length=100)
    filters: tuple[FilterClause, ...] = Field(min_length=1, max_length=100)

    @field_validator("table")
    @classmethod
    def safe_table(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("values")
    @classmethod
    def safe_values(cls, values: dict[str, Any]) -> dict[str, Any]:
        return {_identifier(key): value for key, value in values.items()}


class DeleteRequest(StrictModel):
    table: str
    filters: tuple[FilterClause, ...] = Field(min_length=1, max_length=100)

    @field_validator("table")
    @classmethod
    def safe_table(cls, value: str) -> str:
        return _identifier(value)


__all__ = [
    "RELATIONAL_CAPABILITY_ID",
    "RELATIONAL_MIGRATION_SCHEMA",
    "RelationalMigration",
    "QueryRequest",
    "InsertRequest",
    "UpdateRequest",
    "DeleteRequest",
    "FilterClause",
]
