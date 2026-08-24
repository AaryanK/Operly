"""Source-boundary validation for the relational data capability."""
from __future__ import annotations

import json

from packages.relational_data.contracts import RELATIONAL_CAPABILITY_ID, RelationalMigration
from packages.runtime_plugins.contracts import RuntimeValidation
from packages.runtime_plugins.fullstack_contract import parse_fullstack_manifest


def validate_relational_source(source) -> RuntimeValidation:
    files = getattr(source, "files", source)
    if isinstance(files, dict):
        rows = [(str(path), content) for path, content in files.items()]
    else:
        rows = [
            (str(getattr(item, "path", "")), getattr(item, "content", b""))
            for item in (files or ())
        ]
    by_path = {
        path: content if isinstance(content, bytes) else str(content).encode()
        for path, content in rows
    }
    errors: list[str] = []
    warnings: list[str] = []
    try:
        manifest = parse_fullstack_manifest(source)
    except ValueError as error:
        return RuntimeValidation(False, (str(error),))

    relational = [
        binding
        for binding in manifest.bindings
        if binding.capabilityId == RELATIONAL_CAPABILITY_ID
    ]
    migration_paths = sorted(
        path for path in by_path if path.startswith("migrations/") and path != "migrations/README.md"
    )
    if migration_paths and not relational:
        errors.append("Declarative migrations require a data.relational service binding")
    if relational and len(relational) > 1:
        errors.append("A Solution may declare only one data.relational service binding")

    migrations: list[tuple[str, RelationalMigration]] = []
    for path in migration_paths:
        if not path.endswith(".json"):
            errors.append(f"Relational migrations must be JSON files: {path}")
            continue
        try:
            raw = json.loads(by_path[path].decode("utf-8"))
            migration = RelationalMigration.model_validate(raw)
        except Exception as error:
            errors.append(f"Invalid relational migration {path}: {error}")
            continue
        migrations.append((path, migration))

    versions = [migration.version for _path, migration in migrations]
    if len(versions) != len(set(versions)):
        errors.append("Relational migration versions must be unique")
    if versions:
        ordered = sorted(versions)
        expected = list(range(1, max(ordered) + 1))
        if ordered != expected:
            errors.append("Relational migration history must be contiguous from version 1")
    elif relational:
        warnings.append("data.relational binding has no migration files in this source bundle")

    return RuntimeValidation(not errors, tuple(dict.fromkeys(errors)), tuple(dict.fromkeys(warnings)))


__all__ = ["validate_relational_source"]
