from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from fastapi import APIRouter

import apps.api.main as api_main


IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})
IGNORED_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})


def _rows_from_routes(routes: Iterable[Any], *, source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for route in routes:
        path = str(getattr(route, "path", "") or "")
        endpoint = getattr(route, "endpoint", None)
        module = str(getattr(endpoint, "__module__", "") or "")
        name = str(getattr(endpoint, "__name__", "") or "")
        if not path or path in IGNORED_PATHS:
            continue
        if module == "apps.api.main" and name == "frontend":
            continue
        tags = sorted(str(tag) for tag in (getattr(route, "tags", None) or ()))
        for method in sorted(getattr(route, "methods", set()) or set()):
            method = method.upper()
            if method in IGNORED_METHODS:
                continue
            rows.append(
                {
                    "operation": f"{method} {path}",
                    "method": method,
                    "path": path,
                    "endpoint": f"{module}:{name}",
                    "module": module,
                    "name": name,
                    "tags": tags,
                    "source": source,
                }
            )
    return rows


def route_inventory() -> list[dict[str, Any]]:
    """Return the complete backend operation surface assembled by ``apps.api.main``."""

    rows = _rows_from_routes(getattr(api_main.app, "routes", ()), source="app")
    for symbol, value in sorted(vars(api_main).items()):
        if not symbol.endswith("_router") or not isinstance(value, APIRouter):
            continue
        rows.extend(_rows_from_routes(value.routes, source=symbol))

    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        deduped[(row["operation"], row["endpoint"])] = row
    return sorted(deduped.values(), key=lambda row: (row["operation"], row["endpoint"]))


def route_inventory_digest(rows: Iterable[dict[str, Any]]) -> str:
    """Stable fingerprint of the exact mounted operation/endpoint/source surface."""

    canonical = [
        {
            "operation": str(row["operation"]),
            "endpoint": str(row["endpoint"]),
            "source": str(row["source"]),
        }
        for row in rows
    ]
    encoded = json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


if __name__ == "__main__":
    rows = route_inventory()
    print(f"OPERLY_ROUTE_COUNT={len(rows)}")
    print(f"OPERLY_ROUTE_DIGEST={route_inventory_digest(rows)}")
    for row in rows:
        print(json.dumps(row, separators=(",", ":"), sort_keys=True))
