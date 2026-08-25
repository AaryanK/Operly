"""Canonical machine-readable runtime contracts for coding-agent grounding.

These packets describe contracts enforced by the generated-solution runner. They are
safe to place in model context because they contain schemas and examples only --
never workspace data, credentials, grants, or provider secrets.
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
                    {"name": "id", "type": "uuid", "nullable": False, "primaryKey": True},
                    {"name": "occurred_at", "type": "datetime", "nullable": False},
                ],
            }
        ],
    }
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


def runtime_binding_contract_packet() -> dict[str, Any]:
    """Describe the runner-injected capability endpoint contract.

    The runner writes a credential-free JSON array and exposes its path only through
    OPERLY_BINDINGS_FILE. Generated code never receives runtime grants directly.
    """
    return {
        "environmentVariable": "OPERLY_BINDINGS_FILE",
        "fileShape": [
            {
                "semanticName": "data",
                "capabilityId": "data.relational",
                "required": True,
                "endpoint": "http://127.0.0.1:<runner-owned-port>",
            }
        ],
        "rules": [
            "Read the file path from OPERLY_BINDINGS_FILE at runtime; do not read operly.solution.json as a substitute.",
            "Resolve the row by semanticName/capabilityId and use its injected endpoint field.",
            "Make real HTTP requests to that endpoint. Comments, assertions, local lists/dicts, mocks, or hard-coded IDs never count as consuming a capability.",
            "The local sidecar supplies authorization and upstream routing; generated code must not add provider credentials or runtime grants.",
        ],
        "operations": {
            "data.relational": {
                "methods": {"/query": "POST", "/insert": "POST", "/update": "POST", "/delete": "POST"},
                "authority": "When durable relational state is required, these operations are authoritative. Do not shadow the same migrated tables with module-level in-memory collections.",
            },
            "data.workspace_entities": {
                "methods": {"/schema": "GET", "/list": "POST", "/create": "POST", "/update": "POST", "/{kind}/{entity_id}": "GET"},
                "authority": "Use canonical workspace IDs. /query is not a workspace-entity operation.",
            },
            "identity.app_users": {
                "methods": {"/register": "POST", "/login": "POST", "/session": "POST", "/logout": "POST", "/invitations/accept": "POST"},
                "authority": "Do not replace application identity with hard-coded user allowlists or fake employee IDs.",
            },
        },
    }


def browser_device_contract_packet() -> dict[str, Any]:
    """Ground explicit browser-device requirements in executable browser APIs."""
    return {
        "camera": {
            "requiredEvidence": [
                "navigator.mediaDevices.getUserMedia({video: ...})",
                "a video/capture surface connected to the MediaStream",
                "permission/unsupported/error handling visible to the user",
            ],
            "rule": "A camera requirement cannot be satisfied by a button, text input, comment, or placeholder alone.",
        },
        "qr": {
            "acceptableDecoders": [
                "BarcodeDetector configured for qr_code when the browser supports it",
                "a pinned generated frontend dependency such as jsQR/html5-qrcode/@zxing/qr-scanner",
            ],
            "requiredEvidence": [
                "decoded scanner output reaches the requested domain operation",
                "invalid/unreadable codes are rejected",
                "tests exercise the decode-to-domain-operation boundary without pretending a text button is a scanner",
            ],
        },
    }


def generation_contract_packets() -> dict[str, Any]:
    """Canonical contracts available to the initial coding session."""
    return {
        RELATIONAL_CAPABILITY_ID: relational_migration_contract_packet(),
        "operly.runtime_bindings": runtime_binding_contract_packet(),
        "browser.device_requirements": browser_device_contract_packet(),
    }


def source_contract_repair_packet(error_message: str) -> dict[str, Any]:
    """Return targeted canonical guidance for a deterministic contract failure."""
    text = str(error_message or "")
    lowered = text.lower()
    contracts: dict[str, Any] = {}
    if any(marker in lowered for marker in (
        "relational migration", "relationalmigration", "createtable", "addcolumn",
        "createindex", RELATIONAL_MIGRATION_SCHEMA.lower(),
    )):
        contracts[RELATIONAL_CAPABILITY_ID] = relational_migration_contract_packet()
    if any(marker in lowered for marker in (
        "operly_bindings_file", "capability", "binding", "workspace entit", "app_users",
        "hard-coded", "in-memory", "authoritative runtime",
    )):
        contracts["operly.runtime_bindings"] = runtime_binding_contract_packet()
    if any(marker in lowered for marker in ("camera", "qr", "scanner", "barcode", "getusermedia")):
        contracts["browser.device_requirements"] = browser_device_contract_packet()
    if not contracts:
        return {}
    return {
        "classification": "source_contract_failure",
        "contracts": contracts,
        "instruction": (
            "Repair the actual executable behavior. Treat these contracts as authoritative. "
            "Do not satisfy them with comments, assertions, mocks, local stand-ins, or hard-coded identities."
        ),
    }


__all__ = [
    "browser_device_contract_packet",
    "generation_contract_packets",
    "relational_migration_contract_packet",
    "runtime_binding_contract_packet",
    "source_contract_repair_packet",
]
