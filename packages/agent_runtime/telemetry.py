from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping

_SENSITIVE = ("secret", "token", "password", "credential", "authorization", "cookie", "api_key", "apikey")


def fingerprint(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def _safe(value: Any, *, key: str = "") -> Any:
    lowered = key.lower()
    if any(marker in lowered for marker in _SENSITIVE):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {str(k): _safe(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe(item) for item in list(value)[:50]]
    if isinstance(value, str):
        if len(value) > 500:
            return {"chars": len(value), "sha256_16": fingerprint(value)}
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:200]


def runtime_trace(event: str, **fields: Any) -> None:
    """Emit one compact structured line to stdout for Railway/container logs.

    Raw prompts, context bodies, credentials, cookies and tool payload bodies are not
    logged. Callers log counts, byte sizes, IDs, error codes and stable fingerprints
    instead so production debugging does not become a data-exfiltration surface.
    """
    payload = {
        "ts_unix_ms": int(time.time() * 1000),
        "logger": "operly.agent_runtime",
        "event": str(event or "unknown")[:120],
        **{str(k): _safe(v, key=str(k)) for k, v in fields.items()},
    }
    print("OPERLY_AGENT " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True), flush=True)
