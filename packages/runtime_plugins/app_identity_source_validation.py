"""Source contract for generated applications that use Operly app identity."""
from __future__ import annotations

from typing import Any

from packages.app_identity.contracts import APP_IDENTITY_BINDING_NAME, APP_IDENTITY_CAPABILITY_ID
from packages.runtime_plugins.contracts import RuntimeValidation
from packages.runtime_plugins.fullstack_contract import parse_fullstack_manifest


def validate_app_identity_source(source: Any) -> RuntimeValidation:
    try:
        manifest = parse_fullstack_manifest(source)
    except Exception as error:
        return RuntimeValidation(False, (str(error),))
    bindings = [item for item in manifest.bindings if item.capabilityId == APP_IDENTITY_CAPABILITY_ID]
    errors: list[str] = []
    if len(bindings) > 1:
        errors.append("Exactly one generated-app identity binding is supported per Solution")
    if bindings and bindings[0].semanticName != APP_IDENTITY_BINDING_NAME:
        errors.append(
            f"Generated-app identity binding semanticName must be {APP_IDENTITY_BINDING_NAME}"
        )
    return RuntimeValidation(not errors, tuple(errors))


__all__ = ["validate_app_identity_source"]
