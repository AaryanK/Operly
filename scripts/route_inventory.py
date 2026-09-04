from __future__ import annotations

import json
from typing import Any

from apps.api.main import app


IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})
IGNORED_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})


def route_inventory() -> list[dict[str, Any]]:
    """Return every mounted backend operation except docs and the React catch-all."""

    rows: list[dict[str, Any]] = []
    for route in getattr(app, "routes", ()):
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
            if method in IGNORED_METHODS:
                continue
            rows.append(
                {
                    "operation": f"{method.upper()} {path}",
                    "method": method.upper(),
                    "path": path,
                    "endpoint": f"{module}:{name}",
                    "module": module,
                    "name": name,
                    "tags": tags,
                }
            )
    return sorted(rows, key=lambda row: row["operation"])


if __name__ == "__main__":
    # Compact one-operation-per-line output keeps CI logs searchable even for a large
    # route surface while remaining deterministic enough to snapshot into a contract.
    rows = route_inventory()
    print(f"OPERLY_ROUTE_COUNT={len(rows)}")
    for row in rows:
        print(json.dumps(row, separators=(",", ":"), sort_keys=True))