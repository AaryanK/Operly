from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


# Keep the pre-Agent-Runtime HTTP surface cryptographically pinned while pinning the
# newly reviewed AI ingress as its own exact surface. This avoids weakening the route
# gate merely because Runtime 1.0 intentionally adds web chat/history operations.
EXPECTED_BASE_ROUTE_COUNT = 154
EXPECTED_BASE_ROUTE_DIGEST = "73abc3940eb67cee0caf8898ba1a4704862f33a5db22a1c1ae5d32f34a155599"  # pragma: allowlist secret
EXPECTED_AGENT_RUNTIME_ROUTE_COUNT = 8
EXPECTED_ROUTE_COUNT = EXPECTED_BASE_ROUTE_COUNT + EXPECTED_AGENT_RUNTIME_ROUTE_COUNT
# Backwards-compatible export: this digest intentionally refers to the pinned base
# surface; Runtime 1.0 ingress is pinned independently below.
EXPECTED_ROUTE_DIGEST = EXPECTED_BASE_ROUTE_DIGEST

_AGENT_RUNTIME_SOURCES = frozenset(
    {"personal_agent_runtime_router", "workspace_agent_runtime_router"}
)
_EXPECTED_AGENT_RUNTIME_ROUTES = frozenset(
    {
        (
            "POST /api/agent/chat",
            "apps.api.agent_runtime_router:workspace_chat",
            "workspace_agent_runtime_router",
        ),
        (
            "POST /api/agent/chat-with-attachments",
            "apps.api.agent_runtime_router:workspace_chat_with_attachments",
            "workspace_agent_runtime_router",
        ),
        (
            "GET /api/agent/conversations",
            "apps.api.agent_runtime_router:workspace_conversations",
            "workspace_agent_runtime_router",
        ),
        (
            "GET /api/agent/conversations/{conversation_id}/messages",
            "apps.api.agent_runtime_router:workspace_messages",
            "workspace_agent_runtime_router",
        ),
        (
            "POST /api/personal-agent/chat",
            "apps.api.agent_runtime_router:personal_chat",
            "personal_agent_runtime_router",
        ),
        (
            "POST /api/personal-agent/chat-with-attachments",
            "apps.api.agent_runtime_router:personal_chat_with_attachments",
            "personal_agent_runtime_router",
        ),
        (
            "GET /api/personal-agent/conversations",
            "apps.api.agent_runtime_router:personal_conversations",
            "personal_agent_runtime_router",
        ),
        (
            "GET /api/personal-agent/conversations/{conversation_id}/messages",
            "apps.api.agent_runtime_router:personal_messages",
            "personal_agent_runtime_router",
        ),
    }
)

ALLOWED_CATEGORIES = frozenset(
    {
        "kernel_governed",
        "agent_ingress",
        "semantic_event_ingress",
        "auth_transport",
        "control_plane",
        "read_projection",
        "public_read",
        "data_ingress",
        "legacy_direct",
    }
)


@dataclass(frozen=True, slots=True)
class RouteTraceability:
    category: str
    kernel_governed: bool
    semantic_event_source: bool
    workflow_trigger_identity: bool
    reason: str


# These source-level classifications are intentionally paired with exact route
# fingerprints. A newly added operation cannot silently inherit a broad source rule.
_AUTH_SOURCES = frozenset({"session_router", "discord_auth_router"})
_CONTROL_PLANE_SOURCES = frozenset(
    {
        "access_router",
        "workspace_integrations_router",
        "personal_connectors_router",
        "plugin_platform_router",
        "plugin_event_router",
        "plugin_runtime_management_router",
        "plugin_webhook_management_router",
        "agent_computer_router",
        "runtime_egress_router",
    }
)
_KERNEL_SOURCES = frozenset(
    {
        "kernel_router",
        "personal_tools_router",
        "workspace_tools_router",
        "capability_gateway_router",
    }
)
_PUBLIC_READ_SOURCES = frozenset({"plugin_hosted_public_router", "studio_public_router"})


def _route_digest(rows: list[dict[str, Any]]) -> str:
    canonical = [
        {
            "operation": str(row.get("operation") or ""),
            "endpoint": str(row.get("endpoint") or ""),
            "source": str(row.get("source") or ""),
        }
        for row in rows
    ]
    encoded = json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def classify_route(row: dict[str, Any]) -> RouteTraceability | None:
    source = str(row.get("source") or "")
    method = str(row.get("method") or "").upper()
    path = str(row.get("path") or "")

    if source in _AGENT_RUNTIME_SOURCES:
        return RouteTraceability(
            category="agent_ingress",
            kernel_governed=False,
            semantic_event_source=False,
            workflow_trigger_identity=False,
            reason=(
                "Authenticated Runtime 1.0 model ingress. No-tool responses may finish without Kernel; "
                "every external capability action is independently authorized and executed by Kernel."
            ),
        )

    if source in _KERNEL_SOURCES:
        return RouteTraceability(
            category="kernel_governed",
            kernel_governed=True,
            semantic_event_source=False,
            workflow_trigger_identity=False,
            reason="Transport over the canonical Kernel capability/approval/run substrate.",
        )

    if source == "mcp_router":
        if method == "POST" and path == "/mcp":
            return RouteTraceability(
                category="kernel_governed",
                kernel_governed=True,
                semantic_event_source=False,
                workflow_trigger_identity=False,
                reason="MCP invokes authorized Operly capabilities through the canonical Kernel gateway.",
            )
        return RouteTraceability(
            category="auth_transport",
            kernel_governed=False,
            semantic_event_source=False,
            workflow_trigger_identity=False,
            reason="OAuth discovery/authorization/token transport; not a business-event identity.",
        )

    if source == "plugin_webhook_public_router":
        return RouteTraceability(
            category="semantic_event_ingress",
            kernel_governed=False,
            semantic_event_source=True,
            workflow_trigger_identity=False,
            reason="Verified webhook ingress creates a durable DigitalEvent mirrored to a scoped Kernel semantic event.",
        )

    if source in _AUTH_SOURCES:
        return RouteTraceability(
            category="auth_transport",
            kernel_governed=False,
            semantic_event_source=False,
            workflow_trigger_identity=False,
            reason="Authentication/session transport is intentionally outside workflow trigger identity.",
        )

    if source in _CONTROL_PLANE_SOURCES:
        return RouteTraceability(
            category="control_plane",
            kernel_governed=False,
            semantic_event_source=False,
            workflow_trigger_identity=False,
            reason="Administrative/runtime configuration surface; not a semantic workflow trigger source.",
        )

    if source in _PUBLIC_READ_SOURCES:
        return RouteTraceability(
            category="public_read",
            kernel_governed=False,
            semantic_event_source=False,
            workflow_trigger_identity=False,
            reason="Published/public read surface with no governed mutation semantics.",
        )

    if source == "artifact_router":
        if method == "GET":
            return RouteTraceability(
                category="read_projection",
                kernel_governed=False,
                semantic_event_source=False,
                workflow_trigger_identity=False,
                reason="Artifact listing/download projection; reads do not create semantic workflow events.",
            )
        if method == "POST":
            return RouteTraceability(
                category="data_ingress",
                kernel_governed=False,
                semantic_event_source=False,
                workflow_trigger_identity=False,
                reason="Artifact byte ingress is transport/storage input, not itself a semantic business event.",
            )

    if source == "app":
        if method == "GET" and path in {"/api/health", "/api/rebuild-status"}:
            return RouteTraceability(
                category="read_projection",
                kernel_governed=False,
                semantic_event_source=False,
                workflow_trigger_identity=False,
                reason="Operational diagnostics are read-only projections.",
            )

    if source in {"workspace_os_router", "workspace_simple_router"}:
        if method == "GET":
            return RouteTraceability(
                category="read_projection",
                kernel_governed=False,
                semantic_event_source=False,
                workflow_trigger_identity=False,
                reason="Direct product read projection; safe to expose as read state but not a trigger identity.",
            )
        return RouteTraceability(
            category="legacy_direct",
            kernel_governed=False,
            semantic_event_source=False,
            workflow_trigger_identity=False,
            reason="Direct product mutation still bypasses canonical Kernel capability execution and must not be treated as trigger-safe.",
        )

    return None


def validate_route_traceability(rows: list[dict[str, Any]], *, digest: str) -> list[str]:
    del digest  # The reviewed base and Runtime 1.0 slices are pinned independently below.
    errors: list[str] = []
    if len(rows) != EXPECTED_ROUTE_COUNT:
        errors.append(
            f"route surface changed: expected {EXPECTED_ROUTE_COUNT}, found {len(rows)}; review and repin"
        )

    agent_rows = [row for row in rows if str(row.get("source") or "") in _AGENT_RUNTIME_SOURCES]
    base_rows = [row for row in rows if str(row.get("source") or "") not in _AGENT_RUNTIME_SOURCES]
    if len(base_rows) != EXPECTED_BASE_ROUTE_COUNT:
        errors.append(
            f"base route surface changed: expected {EXPECTED_BASE_ROUTE_COUNT}, found {len(base_rows)}; review and repin"
        )
    base_digest = _route_digest(base_rows)
    if base_digest != EXPECTED_BASE_ROUTE_DIGEST:
        errors.append(
            f"base route fingerprint changed: expected {EXPECTED_BASE_ROUTE_DIGEST}, found {base_digest}; review and repin"
        )

    actual_agent_routes = frozenset(
        (
            str(row.get("operation") or ""),
            str(row.get("endpoint") or ""),
            str(row.get("source") or ""),
        )
        for row in agent_rows
    )
    if actual_agent_routes != _EXPECTED_AGENT_RUNTIME_ROUTES:
        missing = sorted(_EXPECTED_AGENT_RUNTIME_ROUTES - actual_agent_routes)
        extra = sorted(actual_agent_routes - _EXPECTED_AGENT_RUNTIME_ROUTES)
        errors.append(
            "Runtime 1.0 route surface changed; review and repin "
            f"missing={missing!r} extra={extra!r}"
        )

    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("operation") or ""), str(row.get("endpoint") or ""))
        if key in seen:
            errors.append(f"duplicate route inventory row: {key[0]} -> {key[1]}")
            continue
        seen.add(key)
        classification = classify_route(row)
        if classification is None:
            errors.append(
                f"unclassified route: {row.get('operation')} ({row.get('endpoint')}, source={row.get('source')})"
            )
            continue
        if classification.category not in ALLOWED_CATEGORIES:
            errors.append(
                f"invalid route category {classification.category!r}: {row.get('operation')}"
            )
        if classification.workflow_trigger_identity:
            errors.append(
                f"HTTP route may not be a workflow trigger identity: {row.get('operation')}"
            )
        if classification.semantic_event_source and classification.category != "semantic_event_ingress":
            errors.append(
                f"semantic event source must be explicit ingress: {row.get('operation')}"
            )
    return errors


__all__ = [
    "ALLOWED_CATEGORIES",
    "EXPECTED_BASE_ROUTE_COUNT",
    "EXPECTED_BASE_ROUTE_DIGEST",
    "EXPECTED_AGENT_RUNTIME_ROUTE_COUNT",
    "EXPECTED_ROUTE_COUNT",
    "EXPECTED_ROUTE_DIGEST",
    "RouteTraceability",
    "classify_route",
    "validate_route_traceability",
]
