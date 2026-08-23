"""Capability-first architecture manifests for Operly Solutions.

A Solution is defined by the capabilities and surfaces required to satisfy the
owner objective. Runtime kinds (Studio, managed app, generated project) are
compatibility implementations and are deliberately not part of the product
identity exposed to the builder.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class SolutionSurface:
    id: str
    audience: str
    access: str
    purpose: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "audience": self.audience,
            "access": self.access,
            "purpose": self.purpose,
        }


@dataclass(frozen=True, slots=True)
class SolutionCapability:
    id: str
    purpose: str
    implementation: str = "operly_primitive_or_generated"

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "purpose": self.purpose,
            "implementation": self.implementation,
        }


@dataclass(frozen=True, slots=True)
class SolutionManifest:
    name: str
    objective: str
    surfaces: tuple[SolutionSurface, ...]
    capabilities: tuple[SolutionCapability, ...]
    dependency_edges: tuple[tuple[str, str], ...]
    compatibility_runtime: str
    compatibility_reason: str
    schema_version: int = 1

    @property
    def capability_ids(self) -> frozenset[str]:
        return frozenset(item.id for item in self.capabilities)

    @property
    def stateful(self) -> bool:
        return bool(
            self.capability_ids
            & {
                "server.http_api",
                "data.relational",
                "auth.sessions",
                "workflow.state_machine",
                "jobs.background",
                "realtime.events",
                "payments.transactions",
                "storage.files",
                "scheduler.time_slots",
                "tokens.qr",
            }
        )

    def as_dict(self) -> dict:
        return {
            "schemaVersion": self.schema_version,
            "name": self.name,
            "objective": self.objective,
            "surfaces": [item.as_dict() for item in self.surfaces],
            "capabilities": [item.as_dict() for item in self.capabilities],
            "dependencies": [
                {"from": source, "to": target, "type": "requires"}
                for source, target in self.dependency_edges
            ],
            "stateful": self.stateful,
            "compatibilityRuntime": self.compatibility_runtime,
            "compatibilityReason": self.compatibility_reason,
        }

    def builder_contract(self) -> dict:
        """Return the runtime-neutral architecture contract supplied to builders."""
        return {
            "schemaVersion": self.schema_version,
            "surfaces": [item.as_dict() for item in self.surfaces],
            "capabilities": [item.id for item in self.capabilities],
            "dependencies": [list(edge) for edge in self.dependency_edges],
            "constraints": [
                "Implement the requested behavior; do not substitute a brochure or mock UI for stateful requirements.",
                "Use Operly-governed auth, secrets, permissions, and external capabilities rather than embedding credentials.",
                "Preserve tenant boundaries and create verifiable state transitions for workflows.",
            ],
        }


_CAPABILITY_PURPOSES = {
    "ui.public_web": "Public/customer-facing web surface.",
    "ui.workspace_dashboard": "Authenticated staff/workspace operating surface.",
    "ui.primary_app": "Primary authenticated application surface.",
    "server.http_api": "Server-side API and trusted execution boundary.",
    "data.relational": "Durable relational application state.",
    "auth.sessions": "Secure identity and session lifecycle.",
    "auth.roles": "Role/permission-aware access control.",
    "workflow.state_machine": "Durable business-state transitions and invariants.",
    "scheduler.time_slots": "Availability, time-slot, booking, or reservation logic.",
    "jobs.background": "Durable delayed/background work.",
    "notifications.outbound": "Governed outbound customer/staff notifications.",
    "realtime.events": "Live state/event updates between surfaces.",
    "tokens.qr": "Short-lived or scoped QR/token exchange.",
    "payments.transactions": "Payment/transaction capability binding.",
    "storage.files": "Durable file/media storage binding.",
    "integrations.external": "External API/plugin capability bindings.",
}

_DEPENDENCIES = {
    "ui.workspace_dashboard": {"auth.roles"},
    "ui.primary_app": {"auth.sessions"},
    "data.relational": {"server.http_api"},
    "auth.sessions": {"server.http_api"},
    "auth.roles": {"auth.sessions", "server.http_api"},
    "workflow.state_machine": {"data.relational", "server.http_api"},
    "scheduler.time_slots": {"data.relational", "server.http_api"},
    "jobs.background": {"server.http_api"},
    "notifications.outbound": {"server.http_api"},
    "realtime.events": {"server.http_api"},
    "tokens.qr": {"server.http_api"},
    "payments.transactions": {"data.relational", "server.http_api"},
    "storage.files": {"server.http_api"},
    "integrations.external": {"server.http_api"},
}

_STATE_SIGNALS = {
    "book", "booking", "appointment", "appointments", "reservation", "reservations",
    "order", "orders", "pickup", "inventory", "record", "records", "recorder", "save", "store",
    "track", "tracker", "tracking", "log", "logger", "notebook", "status", "queue", "workflow", "manage", "management",
    "checkin", "checkout", "check-in", "check-out", "request", "requests",
}
_AUTH_SIGNALS = {
    "login", "signin", "sign-in", "authentication", "authenticate", "account", "accounts",
}
_ROLE_SIGNALS = {
    "staff", "admin", "administrator", "employee", "employees", "manager", "team", "owner",
    "kitchen", "dispatcher", "technician", "barber", "provider",
}
_PUBLIC_SIGNALS = {
    "public", "customer", "customers", "client", "clients", "guest", "website", "site",
    "booking", "appointment", "reservation", "order", "pickup", "qr", "menu",
}
_SCHEDULING_SIGNALS = {
    "appointment", "appointments", "booking", "book", "reservation", "reservations",
    "availability", "timeslot", "time-slot", "schedule", "scheduling",
}
_WORKFLOW_SIGNALS = {
    "status", "workflow", "pickup", "order", "orders", "queue", "approve", "approval",
    "assigned", "ready", "complete", "completed", "cancel", "cancelled", "canceled",
}
_NOTIFICATION_SIGNALS = {
    "notify", "notification", "notifications", "remind", "reminder", "sms", "text", "email",
    "push", "alert", "alerts",
}
_REALTIME_SIGNALS = {
    "realtime", "real-time", "live", "socket", "websocket", "instantly",
}
_PAYMENT_SIGNALS = {
    "payment", "payments", "pay", "checkout", "stripe", "invoice", "billing",
}
_FILE_SIGNALS = {
    "upload", "uploads", "file", "files", "photo", "photos", "image", "images", "document",
}
_INTEGRATION_SIGNALS = {
    "api", "webhook", "integration", "integrate", "connector", "calendar", "gmail", "slack",
}


def _tokens(value: str) -> set[str]:
    return set(_TOKEN_RE.findall(str(value or "").lower()))


def _has(tokens: set[str], values: Iterable[str]) -> bool:
    return bool(tokens & set(values))


def _dependency_closure(seed: set[str]) -> tuple[set[str], set[tuple[str, str]]]:
    capabilities = set(seed)
    edges: set[tuple[str, str]] = set()
    pending = list(seed)
    while pending:
        capability = pending.pop()
        for dependency in _DEPENDENCIES.get(capability, set()):
            edges.add((capability, dependency))
            if dependency not in capabilities:
                capabilities.add(dependency)
                pending.append(dependency)
    return capabilities, edges


def derive_solution_manifest(name: str, objective: str) -> SolutionManifest:
    """Decompose owner intent into runtime-neutral primitives.

    This is a deterministic safety/fallback decomposition, not a product-template
    classifier. Builders may refine the graph later, but they must preserve these
    minimum required capabilities unless the owner changes the objective.
    """
    clean_name = " ".join(str(name or "").split()).strip()[:200]
    clean_objective = " ".join(str(objective or "").split()).strip()[:8000]
    if not clean_name:
        raise ValueError("Solution name is required")
    if not clean_objective:
        raise ValueError("Describe what this Solution should do")

    tokens = _tokens(f"{clean_name} {clean_objective}")
    seed: set[str] = set()
    surfaces: dict[str, SolutionSurface] = {}

    explicit_public = bool(tokens & {"public", "website", "site", "guest", "qr", "menu", "landing", "homepage", "webpage"})
    customer_interaction = bool(tokens & {"customer", "customers", "client", "clients"}) and bool(
        tokens & {"booking", "book", "appointment", "reservation", "order", "orders", "pickup", "login", "signin", "sign-in"}
    )
    public_needed = explicit_public or customer_interaction
    staff_needed = _has(tokens, _ROLE_SIGNALS) or "dashboard" in tokens or "backoffice" in tokens
    state_needed = _has(tokens, _STATE_SIGNALS)
    auth_needed = _has(tokens, _AUTH_SIGNALS)

    if public_needed:
        seed.add("ui.public_web")
        surfaces["public_web"] = SolutionSurface(
            "public_web", "customer_or_public", "public_or_scoped_token", "Customer/public interaction surface."
        )
    if staff_needed:
        seed.add("ui.workspace_dashboard")
        surfaces["workspace_dashboard"] = SolutionSurface(
            "workspace_dashboard", "workspace_staff", "authenticated", "Staff/admin operating surface."
        )

    if state_needed:
        seed.update({"server.http_api", "data.relational"})
    if auth_needed:
        seed.add("auth.sessions")
    if auth_needed and staff_needed:
        seed.add("auth.roles")
    if _has(tokens, _SCHEDULING_SIGNALS):
        seed.update({"scheduler.time_slots", "workflow.state_machine"})
    if _has(tokens, _WORKFLOW_SIGNALS):
        seed.add("workflow.state_machine")
    if _has(tokens, _NOTIFICATION_SIGNALS):
        seed.update({"notifications.outbound", "jobs.background"})
    if _has(tokens, _REALTIME_SIGNALS):
        seed.add("realtime.events")
    if "qr" in tokens or "qrcode" in tokens or "qr-code" in tokens:
        seed.add("tokens.qr")
        if auth_needed:
            seed.add("auth.sessions")
    if _has(tokens, _PAYMENT_SIGNALS):
        seed.add("payments.transactions")
    if _has(tokens, _FILE_SIGNALS):
        seed.add("storage.files")
    if _has(tokens, _INTEGRATION_SIGNALS):
        seed.add("integrations.external")

    if seed & {
        "data.relational", "workflow.state_machine", "scheduler.time_slots",
        "notifications.outbound", "payments.transactions", "storage.files",
    } and not surfaces:
        seed.add("ui.primary_app")
        surfaces["primary_app"] = SolutionSurface(
            "primary_app", "workspace_user", "authenticated", "Primary application operating surface."
        )

    if not seed:
        if tokens & {"landing", "homepage", "portfolio", "brochure", "marketing", "seo", "webpage"}:
            seed.add("ui.public_web")
            surfaces["public_web"] = SolutionSurface(
                "public_web", "public", "public", "Public digital-presence surface."
            )
        else:
            seed.update({"ui.primary_app", "server.http_api", "data.relational"})
            surfaces["primary_app"] = SolutionSurface(
                "primary_app", "workspace_user", "authenticated", "Primary application operating surface."
            )

    capabilities, edges = _dependency_closure(seed)
    backend_required = bool(
        capabilities
        & {
            "server.http_api", "data.relational", "auth.sessions", "workflow.state_machine",
            "scheduler.time_slots", "jobs.background", "notifications.outbound", "realtime.events",
            "tokens.qr", "payments.transactions", "storage.files", "integrations.external",
        }
    )
    compatibility_runtime = "managed_app" if backend_required else "studio"
    compatibility_reason = (
        "Current managed-app runtime is the compatibility implementation for a capability graph that requires trusted server/state primitives."
        if backend_required
        else "Current Studio runtime is sufficient because the capability graph is presentation-only."
    )

    ordered_capabilities = tuple(
        SolutionCapability(capability, _CAPABILITY_PURPOSES[capability])
        for capability in sorted(capabilities)
    )
    return SolutionManifest(
        name=clean_name,
        objective=clean_objective,
        surfaces=tuple(surfaces[key] for key in sorted(surfaces)),
        capabilities=ordered_capabilities,
        dependency_edges=tuple(sorted(edges)),
        compatibility_runtime=compatibility_runtime,
        compatibility_reason=compatibility_reason,
    )
