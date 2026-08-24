"""Source validation for generated Solutions consuming canonical workspace entities."""
from __future__ import annotations

import json
from typing import Any

from packages.runtime_plugins.contracts import RuntimeValidation
from packages.runtime_plugins.fullstack_contract import parse_fullstack_manifest
from packages.workspace_entities.contracts import (
    WORKSPACE_ENTITY_CAPABILITY_ID,
    WORKSPACE_ENTITY_MANIFEST,
    WorkspaceEntityManifest,
)

_PRIVATE_TABLE_NAMES = {
    "employee": {"employee", "employees"},
    "customer": {"customer", "customers"},
    "location": {"location", "locations"},
}


def _files(source: Any) -> dict[str, bytes]:
    rows = getattr(source, "files", source)
    if isinstance(rows, dict):
        return {str(k): (v if isinstance(v, bytes) else str(v).encode()) for k, v in rows.items()}
    return {str(getattr(item, "path", "")): getattr(item, "content", b"") for item in (rows or ())}


def parse_workspace_entity_manifest(source: Any) -> WorkspaceEntityManifest | None:
    payload = _files(source).get(WORKSPACE_ENTITY_MANIFEST)
    if payload is None:
        return None
    try:
        return WorkspaceEntityManifest.model_validate(json.loads(payload.decode("utf-8")))
    except Exception as error:
        raise ValueError(f"{WORKSPACE_ENTITY_MANIFEST} is invalid: {error}") from error


def validate_workspace_entity_source(source: Any) -> RuntimeValidation:
    errors: list[str] = []
    try:
        fullstack = parse_fullstack_manifest(source)
    except Exception as error:
        return RuntimeValidation(False, (str(error),))
    bindings = [item for item in fullstack.bindings if item.capabilityId == WORKSPACE_ENTITY_CAPABILITY_ID]
    try:
        declaration = parse_workspace_entity_manifest(source)
    except ValueError as error:
        return RuntimeValidation(False, (str(error),))
    if declaration is None:
        if bindings:
            errors.append(f"{WORKSPACE_ENTITY_CAPABILITY_ID} requires {WORKSPACE_ENTITY_MANIFEST}")
        return RuntimeValidation(not errors, tuple(errors))
    if not bindings:
        errors.append(f"{WORKSPACE_ENTITY_MANIFEST} requires a {WORKSPACE_ENTITY_CAPABILITY_ID} binding")
    if len(bindings) > 1:
        errors.append("Exactly one workspace entity graph binding is supported per Solution")

    declared_kinds = {item.kind for item in declaration.entities}
    # If a Solution claims a canonical entity, it must not also create an app-private
    # table with the same business meaning. This is the anti-silo invariant.
    for path, payload in _files(source).items():
        if not path.startswith("migrations/") or not path.endswith(".json"):
            continue
        try:
            raw = json.loads(payload.decode("utf-8"))
        except Exception:
            continue
        for operation in raw.get("operations") or []:
            if operation.get("op") != "create_table":
                continue
            table = str(operation.get("table") or "").lower()
            for kind in declared_kinds:
                if table in _PRIVATE_TABLE_NAMES[kind]:
                    errors.append(
                        f"Canonical {kind} is declared through {WORKSPACE_ENTITY_CAPABILITY_ID}; do not create app-private table {table}"
                    )
    return RuntimeValidation(not errors, tuple(dict.fromkeys(errors)))


__all__ = ["parse_workspace_entity_manifest", "validate_workspace_entity_source"]
