"""Canonical workspace-scoped business entities shared by generated Solutions.

Entity kinds are owned by Operly, not redefined per application. Generated source
explicitly declares which kinds it consumes in ``operly.entities.json`` and receives
one semantic binding per canonical kind at runtime.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

WORKSPACE_ENTITY_CAPABILITY_ID = "data.workspace_entities"
WORKSPACE_ENTITY_SCHEMA_VERSION = "operly.workspace-entities/v1"
WORKSPACE_ENTITY_MANIFEST = "operly.entities.json"
EntityKind = Literal["employee", "customer", "location"]
EntityAccess = Literal["read", "write"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntityUse(StrictModel):
    semanticName: str
    kind: EntityKind
    access: tuple[EntityAccess, ...] = ("read", "write")

    @model_validator(mode="after")
    def canonical_binding(self):
        if self.semanticName != self.kind:
            raise ValueError("Workspace entity v1 semanticName must equal the canonical entity kind")
        if not self.access or len(self.access) != len(set(self.access)):
            raise ValueError("Entity access values must be non-empty and unique")
        return self


class WorkspaceEntityManifest(StrictModel):
    schemaVersion: Literal["operly.workspace-entities/v1"] = WORKSPACE_ENTITY_SCHEMA_VERSION
    entities: tuple[EntityUse, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_entities(self):
        kinds = [item.kind for item in self.entities]
        if len(kinds) != len(set(kinds)):
            raise ValueError("Each canonical entity kind may be declared once")
        return self


class EntityCreate(StrictModel):
    kind: EntityKind
    values: dict[str, Any] = Field(min_length=1, max_length=32)


class EntityUpdate(StrictModel):
    kind: EntityKind
    entityId: str = Field(min_length=1, max_length=120)
    values: dict[str, Any] = Field(min_length=1, max_length=32)


class EntityList(StrictModel):
    kind: EntityKind
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    status: str | None = Field(default=None, max_length=40)
    locationId: str | None = Field(default=None, max_length=120)


CANONICAL_ENTITY_SCHEMAS: dict[str, dict[str, Any]] = {
    "location": {
        "fields": {
            "id": {"type": "uuid", "required": True, "immutable": True},
            "name": {"type": "string", "required": True},
            "code": {"type": "string", "required": False},
            "timezone": {"type": "string", "required": False},
            "status": {"type": "string", "required": True, "default": "active"},
            "metadata": {"type": "json", "required": False},
        },
        "relations": {},
    },
    "employee": {
        "fields": {
            "id": {"type": "uuid", "required": True, "immutable": True},
            "display_name": {"type": "string", "required": True},
            "email": {"type": "string", "required": False},
            "phone": {"type": "string", "required": False},
            "status": {"type": "string", "required": True, "default": "active"},
            "location_id": {"type": "uuid", "required": False},
            "metadata": {"type": "json", "required": False},
        },
        "relations": {"location_id": {"kind": "location", "cardinality": "many_to_one"}},
    },
    "customer": {
        "fields": {
            "id": {"type": "uuid", "required": True, "immutable": True},
            "display_name": {"type": "string", "required": True},
            "email": {"type": "string", "required": False},
            "phone": {"type": "string", "required": False},
            "status": {"type": "string", "required": True, "default": "active"},
            "location_id": {"type": "uuid", "required": False},
            "metadata": {"type": "json", "required": False},
        },
        "relations": {"location_id": {"kind": "location", "cardinality": "many_to_one"}},
    },
}


__all__ = [
    "WORKSPACE_ENTITY_CAPABILITY_ID",
    "WORKSPACE_ENTITY_SCHEMA_VERSION",
    "WORKSPACE_ENTITY_MANIFEST",
    "WorkspaceEntityManifest",
    "EntityCreate",
    "EntityUpdate",
    "EntityList",
    "CANONICAL_ENTITY_SCHEMAS",
]
