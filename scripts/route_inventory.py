from __future__ import annotations

import json
from typing import Any

from apps.api.main import app


IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})


def route_inventory() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for route in getattr(app, "routes", ()):
        path = str(getattr(route, "path", "") or "")
        if not path.startswith("/api/"):
            continue
        endpoint = getattr(route, "endpoint", None)
        module = str(getattr(endpoint, "__module__", "") or "")
        name = str(getattr(endpoint, "__name__", "") or "")
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
    print(json.dumps(route_inventory(), indent=2, sort_keys=True))