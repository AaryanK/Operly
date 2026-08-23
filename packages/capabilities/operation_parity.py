"""Fail-closed UI/API operation -> agent capability parity contract.

Every user-facing operation under a governed API prefix must be represented here as
one of:

* one or more canonical capability IDs that provide equivalent governed agent use;
* an explicit, categorized exemption explaining why agent exposure is inappropriate.

The validator introspects FastAPI's registered routes, so adding a new endpoint under
a governed prefix without updating this source of truth fails CI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from packages.capabilities.defaults import default_registry


GOVERNED_PREFIXES = (
    "/api/business",
    "/api/company",
    "/api/actions",
    "/api/solutions",
    "/api/application-builder",
    "/api/studio",
    "/api/workspace",
    "/api/operations",
    "/api/integrations",
    "/api/connectors",
    "/api/personal",
    "/api/access",
    "/api/mcp",
)

ALLOWED_EXEMPTION_CATEGORIES = frozenset(
    {
        "authentication_or_session",
        "binary_transport",
        "debug_or_diagnostics",
        "human_approval_ui",
        "human_settings_ui",
        "internal_runtime",
        "public_unauthenticated",
        "transport_adapter",
    }
)


@dataclass(frozen=True, slots=True)
class OperationContract:
    method: str
    path: str
    capabilities: tuple[str, ...] = ()
    exemption_category: str | None = None
    exemption_reason: str | None = None

    @property
    def key(self) -> str:
        return f"{self.method.upper()} {self.path}"


# Populated from the current registered route inventory. The validator deliberately
# starts fail-closed: entries may never be inferred from a URL prefix alone.
OPERATION_CONTRACTS: tuple[OperationContract, ...] = ()


def _route_keys(app: Any) -> dict[str, str]:
    rows: dict[str, str] = {}
    for route in getattr(app, "routes", ()):  # FastAPI APIRoute + mounted/static routes
        path = str(getattr(route, "path", "") or "")
        if not any(path.startswith(prefix) for prefix in GOVERNED_PREFIXES):
            continue
        module = str(getattr(getattr(route, "endpoint", None), "__module__", ""))
        name = str(getattr(getattr(route, "endpoint", None), "__name__", ""))
        for method in sorted(getattr(route, "methods", set()) or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            rows[f"{method} {path}"] = f"{module}:{name}"
    return rows


def operation_inventory(app: Any) -> list[dict[str, str]]:
    return [
        {"operation": key, "endpoint": endpoint}
        for key, endpoint in sorted(_route_keys(app).items())
    ]


def validate_operation_parity(app: Any) -> list[str]:
    errors: list[str] = []
    routes = _route_keys(app)
    registry_ids = {definition.id for definition in default_registry().definitions()}

    contracts: dict[str, OperationContract] = {}
    for contract in OPERATION_CONTRACTS:
        if contract.key in contracts:
            errors.append(f"duplicate operation contract: {contract.key}")
            continue
        contracts[contract.key] = contract
        has_capabilities = bool(contract.capabilities)
        has_exemption = bool(contract.exemption_category or contract.exemption_reason)
        if has_capabilities == has_exemption:
            errors.append(
                f"{contract.key}: declare capabilities OR one explicit exemption, not both/neither"
            )
            continue
        if has_capabilities:
            missing = sorted(set(contract.capabilities) - registry_ids)
            if missing:
                errors.append(
                    f"{contract.key}: unknown canonical capabilities: {', '.join(missing)}"
                )
        else:
            if contract.exemption_category not in ALLOWED_EXEMPTION_CATEGORIES:
                errors.append(
                    f"{contract.key}: invalid exemption category {contract.exemption_category!r}"
                )
            if len(str(contract.exemption_reason or "").strip()) < 12:
                errors.append(f"{contract.key}: exemption reason is too vague")

    for key, endpoint in sorted(routes.items()):
        if key not in contracts:
            errors.append(f"unmapped operation: {key} ({endpoint})")
    for key in sorted(set(contracts) - set(routes)):
        errors.append(f"stale operation contract: {key}")

    return errors
