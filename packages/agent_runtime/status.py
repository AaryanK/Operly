"""Lightweight DragonZpyder-only availability diagnostics.

Keep this module dependency-free: plugin and health routes import it even
when the heavyweight agent/MCP SDK dependencies are not installed.
The actual delegations perform independent strict endpoint validation.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit


def _endpoint_present(name: str, path: str) -> bool:
    raw = os.getenv(name, "").strip()
    try:
        p = urlsplit(raw)
        public = (p.scheme == "https" and p.hostname == "api.dragonzpyder.xyz"
                  and p.port in (None, 443))
        private = (p.scheme == "http" and bool(p.hostname)
                   and p.hostname.endswith(".railway.internal")
                   and p.port in (None, 8080))
        return bool((public or private) and p.path == path and not (
            p.username or p.password or p.query or p.fragment))
    except ValueError:
        return False


def agent_runtime_status() -> dict[str, object]:
    token = os.getenv("OPERLY_DRAGONZPYDER_PERSONAL_AGENT_TOKEN", "")
    proof = os.getenv("OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET", "")
    service_configured = len(token) >= 40 and len(proof) >= 40
    personal = service_configured and _endpoint_present(
        "OPERLY_DRAGONZPYDER_PERSONAL_RUN_URL",
        "/internal/agents/operly/personal/run",
    )
    workspace = service_configured and _endpoint_present(
        "OPERLY_DRAGONZPYDER_WORKSPACE_RUN_URL",
        "/internal/agents/operly/workspace/run",
    )
    return {
        "enabled": True,
        "configured": personal and workspace,
        "provider": "dragonzpyder",
        "model": None,
        "personal_configured": personal,
        "workspace_configured": workspace,
        "reason": None if personal and workspace else (
            "DragonZpyder delegated endpoint or service credentials unavailable"
        ),
    }
