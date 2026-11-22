"""DragonZpyder-only delegated agent availability (not local model status)."""
import os

from packages.agent_runtime.dragonzpyder_personal import (
    DragonZpyderPersonalUnavailable, _endpoint as personal_endpoint,
)
from packages.agent_runtime.dragonzpyder_workspace import (
    DragonZpyderWorkspaceUnavailable, _endpoint as workspace_endpoint,
)


def agent_runtime_status() -> dict[str, object]:
    service_token = os.getenv("OPERLY_DRAGONZPYDER_PERSONAL_AGENT_TOKEN", "")
    service_proof = os.getenv("OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET", "")
    has_proof = len(service_token) >= 40 and len(service_proof) >= 40
    try:
        personal_endpoint()
        personal = has_proof
    except DragonZpyderPersonalUnavailable:
        personal = False
    try:
        workspace_endpoint()
        workspace = has_proof
    except DragonZpyderWorkspaceUnavailable:
        workspace = False
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
