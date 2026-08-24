"""Canonical machine-readable runtime contracts for coding-agent grounding.

These packets are derived from the same Pydantic contracts that protect the runtime
boundary.  They are safe to place in model context because they contain schemas and
examples only -- never workspace data, credentials, grants, or provider secrets.
"""
from __future__ import annotations

from typing import Any

from packages.relational_data.contracts import (
    RELATIONAL_CAPABILITY_ID,
    RELATIONAL_MIGRATION_SCHEMA,
    RelationalMigration,
)


def _relational_example() -> dict[str, Any]:
    example = {
        "schemaVersion": RELATIONAL_MIGRATION_SCHEMA,
        "version": 1,
        "name": "initial records",
        "operations": [
            {
                "op": "create_table",
                "table": "records",
                "columns": [
                    {
                        "name": "id",
                        "type": "uuid",
                        "nullable": False,
                        "primaryKey": True,
                    },
                    {
                        "name": "occurred_at",
                        "type": "datetime",
                        "nullable": False,
                    },
                ],
            }
        ],
    }
    # Keep the example executable proof of the contract rather than hand-written
    # documentation that can silently drift away from the validator.
    return RelationalMigration.model_validate(example).model_dump(mode="json")


def relational_migration_contract_packet() -> dict[str, Any]:
    """Return the exact relational migration schema plus a validated example."""
    return {
        "capabilityId": RELATIONAL_CAPABILITY_ID,
        "schemaVersion": RELATIONAL_MIGRATION_SCHEMA,
        "migrationJsonSchema": RelationalMigration.model_json_schema(),
        "canonicalMigrationExample": _relational_example(),
        "repairRules": [
            "Every migration operation is discriminated by its op field.",
            "create_table requires table and columns.",
            "Column type belongs on each object inside columns; it is never a create_table field.",
            "add_column requires table and one column object.",
            "create_index requires table, name, and columns.",
            "Do not emit raw SQL or provider credentials.",
        ],
    }


def generation_contract_packets() -> dict[str, Any]:
    """Contracts that generated full-stack source may need during Phase 0.

    This deliberately starts with the relational primitive that failed in the live
    Studio acceptance case.  Additional capability contracts can be added here as
    they become first-class describable runtime capabilities.
    """
    return {
        RELATIONAL_CAPABILITY_ID: relational_migration_contract_packet(),
    }


def source_contract_repair_packet(error_message: str) -> dict[str, Any]:
    """Return targeted canonical guidance for a deterministic contract failure."""
    text = str(error_message or "")
    lowered = text.lower()
    relational_markers = (
        "relational migration",
        "relationalmigration",
        "createtable",
        "addcolumn",
        "createindex",
        RELATIONAL_MIGRATION_SCHEMA.lower(),
    )
    if not any(marker in lowered for marker in relational_markers):
        return {}
    return {
        "classification": "relational_migration_contract_failure",
        "contracts": {
            RELATIONAL_CAPABILITY_ID: relational_migration_contract_packet(),
        },
        "instruction": (
            "Repair only the invalid migration/source contract while preserving product behavior. "
            "Treat the supplied JSON schema as authoritative; do not guess an alternate shape."
        ),
    }


__all__ = [
    "generation_contract_packets",
    "relational_migration_contract_packet",
    "source_contract_repair_packet",
]
