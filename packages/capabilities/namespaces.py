"""Scope-aware hierarchical namespace for model-facing capability discovery.

The CapabilityRegistry remains the fine-grained execution/security catalog. This tree
is the much smaller navigation surface shown to a model. Registering a provider does
not automatically make its operations model-visible: product areas are mounted here
explicitly, one branch at a time.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from packages.security.surfaces import SurfaceKind


@dataclass(frozen=True, slots=True)
class CapabilityNamespaceNode:
    id: str
    label: str
    description: str
    parent_id: str | None
    aliases: tuple[str, ...] = ()
    capability_ids: tuple[str, ...] = ()

    @property
    def depth(self) -> int:
        return self.id.count(".")

    def search_document(self) -> str:
        return " ".join((self.id, self.label, self.description, *self.aliases)).lower()


def _node(
    id: str,
    label: str,
    description: str,
    parent: str | None,
    *,
    aliases: tuple[str, ...] = (),
    capabilities: tuple[str, ...] = (),
) -> CapabilityNamespaceNode:
    return CapabilityNamespaceNode(id, label, description, parent, aliases, capabilities)


def _google_nodes(prefix: str) -> tuple[CapabilityNamespaceNode, ...]:
    google = f"{prefix}.google"
    gmail = f"{google}.gmail"
    calendar = f"{google}.calendar"
    return (
        _node(
            google,
            "Google",
            "Google services authorized in this scope.",
            prefix,
            aliases=("google account", "google workspace"),
        ),
        _node(
            gmail,
            "Gmail",
            "Email capabilities for the Google connection authorized in this scope.",
            google,
            aliases=("email", "mail", "inbox", "gmail"),
        ),
        _node(
            f"{gmail}.messages",
            "Messages",
            "Find, read, send, and organize Gmail messages and threads.",
            gmail,
            aliases=("email message", "thread", "send email", "labels", "archive", "read unread"),
            capabilities=(
                "gmail.search",
                "gmail.read_message",
                "gmail.read_thread",
                "gmail.modify_labels",
                "gmail.send_email",
            ),
        ),
        _node(
            f"{gmail}.drafts",
            "Drafts",
            "Create, retrieve, update, send, or delete Gmail drafts.",
            gmail,
            aliases=("draft", "compose", "unsent email"),
            capabilities=(
                "gmail.create_draft",
                "gmail.create_draft_with_artifacts",
                "gmail.list_drafts",
                "gmail.get_draft",
                "gmail.update_draft",
                "gmail.send_draft",
                "gmail.delete_draft",
            ),
        ),
        _node(
            f"{gmail}.attachments",
            "Attachments",
            "List and read Gmail message attachments.",
            gmail,
            aliases=("attachment", "email file"),
            capabilities=(
                "gmail.list_attachments",
                "gmail.read_attachment",
            ),
        ),
        _node(
            calendar,
            "Google Calendar",
            "Calendar capabilities for the Google connection authorized in this scope.",
            google,
            aliases=("calendar", "schedule", "meeting"),
        ),
        _node(
            f"{calendar}.events",
            "Events",
            "Retrieve, create, update, or delete Google Calendar events.",
            calendar,
            aliases=("event", "meeting", "appointment", "schedule"),
            capabilities=(
                "calendar.list_events",
                "calendar.create_event",
                "calendar.update_event",
                "calendar.delete_event",
            ),
        ),
        _node(
            f"{calendar}.availability",
            "Availability",
            "Check free/busy information and assess schedule conflicts.",
            calendar,
            aliases=("free busy", "conflict", "available", "availability", "deadline"),
            capabilities=(
                "calendar.freebusy",
                "calendar.assess_deadline_conflicts",
            ),
        ),
        _node(
            f"{calendar}.calendars",
            "Calendars",
            "Retrieve calendars available to the connected Google account.",
            calendar,
            aliases=("calendar list", "calendar account"),
            capabilities=("calendar.list_calendars",),
        ),
    )


_NODES = (
    # Personal/account root. Chats are intentionally absent: conversation history is
    # application context, not a model capability namespace.
    _node(
        "user",
        "User",
        "The authenticated person's private Operly account surface.",
        None,
        aliases=("personal", "account", "me"),
    ),
    _node(
        "user.settings",
        "Settings",
        "Private user-level preferences and account settings.",
        "user",
        aliases=("preferences", "profile settings"),
    ),
    _node(
        "user.connections",
        "Connections",
        "Private services and accounts connected by the user.",
        "user",
        aliases=("connectors", "integrations", "linked accounts"),
        capabilities=("account.list_personal_connectors",),
    ),
    *_google_nodes("user.connections"),
    _node(
        "user.workspaces",
        "Workspaces",
        "Workspaces the authenticated user belongs to. Workspace internals are reached through an explicit governed workspace delegation, never by changing the private conversation scope.",
        "user",
        aliases=("servers", "tenants", "businesses", "organizations"),
    ),
    _node(
        "user.workspaces.manage",
        "Workspace Management",
        "List, create, update, or inspect workspaces belonging to the authenticated user.",
        "user.workspaces",
        aliases=("list workspace", "create workspace", "workspace overview", "workspace settings"),
        capabilities=(
            "account.list_workspaces",
            "account.create_workspace",
            "account.update_workspace",
            "account.workspace_overview",
        ),
    ),
    _node(
        "user.workspaces.delegate",
        "Workspace Delegation",
        "Inspect authorized workspace operations and explicitly delegate one governed operation into a chosen workspace.",
        "user.workspaces",
        aliases=("workspace capability", "act in workspace", "delegate workspace"),
        capabilities=(
            "account.workspace_capabilities",
            "account.workspace_execute",
        ),
    ),

    # Workspace root. Personal account branches never appear beneath it.
    _node(
        "workspace",
        "Workspace",
        "The currently authorized Operly workspace.",
        None,
        aliases=("business", "tenant", "organization"),
    ),
    _node(
        "workspace.crm",
        "CRM",
        "Workspace customer, contact, lead, and pipeline operations.",
        "workspace",
        aliases=("customer", "contact", "lead", "pipeline", "sales"),
    ),
    _node(
        "workspace.operations",
        "Operations",
        "Workspace operational work and business processes.",
        "workspace",
        aliases=("tasks", "orders", "inventory", "workflow", "business operations"),
    ),
    _node(
        "workspace.activity",
        "Activity",
        "Workspace action, event, and activity history.",
        "workspace",
        aliases=("events", "audit", "history", "actions"),
    ),
    _node(
        "workspace.presence",
        "Presence",
        "Workspace presence and real-time participant state.",
        "workspace",
        aliases=("online", "status", "participants"),
    ),
    _node(
        "workspace.solutions",
        "Solutions",
        "Software and solutions owned by the current workspace.",
        "workspace",
        aliases=("apps", "applications", "websites", "software"),
    ),
    _node(
        "workspace.solutions.studio",
        "Studio",
        "Build and manage canonical Operly software projects.",
        "workspace.solutions",
        aliases=("software studio", "website studio", "app builder", "code"),
    ),
    _node(
        "workspace.solutions.studio.projects",
        "Projects",
        "Create, retrieve, and inspect canonical Studio software projects.",
        "workspace.solutions.studio",
        aliases=("project", "solution project"),
        capabilities=(
            "software.project.list",
            "software.project.create",
            "software.project.inspect",
        ),
    ),
    _node(
        "workspace.solutions.studio.build",
        "Build",
        "Build or edit software, retrieve durable build progress, and export canonical source.",
        "workspace.solutions.studio",
        aliases=("generate app", "build app", "edit app", "source", "build status"),
        capabilities=(
            "software.build",
            "software.edit",
            "software.build.status",
            "software.source.export",
        ),
    ),
    _node(
        "workspace.solutions.studio.bindings",
        "Bindings",
        "Retrieve or configure governed service bindings for a software project.",
        "workspace.solutions.studio",
        aliases=("service binding", "project integration"),
        capabilities=(
            "software.binding.list",
            "software.binding.create",
            "software.binding.revoke",
        ),
    ),
    _node(
        "workspace.connections",
        "Connections",
        "External services connected to the current workspace.",
        "workspace",
        aliases=("connectors", "integrations", "linked services"),
    ),
    *_google_nodes("workspace.connections"),
    _node(
        "workspace.plugins",
        "Plugins",
        "Plugins installed or configured for the current workspace.",
        "workspace",
        aliases=("extensions", "capabilities"),
    ),
    _node(
        "workspace.members_roles",
        "Members and Roles",
        "Workspace members, invitations, roles, and role permissions.",
        "workspace",
        aliases=("members", "roles", "permissions", "invite", "access"),
    ),
    _node(
        "workspace.ai",
        "AI",
        "Workspace AI configuration and agent behavior.",
        "workspace",
        aliases=("agent", "model", "assistant"),
    ),
    _node(
        "workspace.mcp",
        "MCP",
        "Workspace MCP clients and capability access configuration.",
        "workspace",
        aliases=("model context protocol", "client"),
    ),
    _node(
        "workspace.settings",
        "Settings",
        "Workspace-level settings and presentation configuration.",
        "workspace",
        aliases=("preferences", "configuration", "timezone"),
    ),
)


class CapabilityNamespaceTree:
    def __init__(self, nodes: Iterable[CapabilityNamespaceNode] = _NODES) -> None:
        self._nodes = {node.id: node for node in nodes}
        self._children: dict[str, tuple[str, ...]] = {}
        for node in self._nodes.values():
            if node.parent_id:
                current = list(self._children.get(node.parent_id, ()))
                current.append(node.id)
                self._children[node.parent_id] = tuple(current)

    @staticmethod
    def root_for(surface: SurfaceKind | str | None) -> str:
        return "user" if SurfaceKind.coerce(surface).allows_personal_global else "workspace"

    def node(self, namespace_id: str) -> CapabilityNamespaceNode:
        clean = str(namespace_id or "").strip().lower()
        try:
            return self._nodes[clean]
        except KeyError as exc:
            raise LookupError(f"Unknown capability namespace: {clean}") from exc

    def allowed(self, namespace_id: str, surface: SurfaceKind | str | None) -> bool:
        root = self.root_for(surface)
        clean = str(namespace_id or "").strip().lower()
        return clean == root or clean.startswith(root + ".")

    def children(
        self,
        namespace_id: str,
        surface: SurfaceKind | str | None,
    ) -> list[CapabilityNamespaceNode]:
        if not self.allowed(namespace_id, surface):
            return []
        return [self._nodes[item] for item in self._children.get(namespace_id, ())]

    def leaf_ids(self, namespace_id: str, eligible_ids: Iterable[str]) -> list[str]:
        eligible = set(eligible_ids)
        node = self.node(namespace_id)
        return [capability_id for capability_id in node.capability_ids if capability_id in eligible]

    def descendant_leaf_ids(self, namespace_id: str, eligible_ids: Iterable[str]) -> set[str]:
        eligible = set(eligible_ids)
        output = set(self.leaf_ids(namespace_id, eligible))
        for child in self._children.get(namespace_id, ()):
            output.update(self.descendant_leaf_ids(child, eligible))
        return output

    def row(
        self,
        node: CapabilityNamespaceNode,
        *,
        surface: SurfaceKind | str | None,
        eligible_ids: Iterable[str],
    ) -> dict:
        eligible = set(eligible_ids)
        children = self.children(node.id, surface)
        direct = self.leaf_ids(node.id, eligible)
        descendant = self.descendant_leaf_ids(node.id, eligible)
        return {
            "id": node.id,
            "label": node.label,
            "description": node.description,
            "depth": node.depth,
            "parent_id": node.parent_id,
            "child_count": len(children),
            "operation_count": len(descendant),
            "available_here": len(direct),
            "terminal": not children,
            "implemented": bool(descendant),
        }

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {
            item
            for item in re.findall(r"[a-z0-9]+", str(value or "").lower())
            if len(item) > 1
        }

    def search(
        self,
        query: str,
        *,
        surface: SurfaceKind | str | None,
        eligible_ids: Iterable[str],
        limit: int = 8,
    ) -> list[dict]:
        root = self.root_for(surface)
        query_text = " ".join(str(query or "").lower().split())
        query_tokens = self._tokens(query_text)
        eligible = set(eligible_ids)
        scored: list[tuple[float, CapabilityNamespaceNode]] = []
        for node in self._nodes.values():
            if node.id == root or not self.allowed(node.id, surface):
                continue
            document = node.search_document()
            document_tokens = self._tokens(document)
            score = 0.0
            if query_text and query_text in document:
                score += 8.0
            if query_tokens:
                overlap = query_tokens & document_tokens
                score += 2.0 * len(overlap)
                if query_tokens.issubset(document_tokens):
                    score += 3.0
            direct_count = len(self.leaf_ids(node.id, eligible))
            descendant_count = len(self.descendant_leaf_ids(node.id, eligible))
            score += min(direct_count, 3) * 0.35
            score += min(descendant_count, 5) * 0.08
            if score > 0:
                scored.append((score, node))
        scored.sort(key=lambda item: (-item[0], item[1].depth, item[1].id))
        rows = []
        for score, node in scored[: max(1, min(int(limit or 8), 12))]:
            row = self.row(node, surface=surface, eligible_ids=eligible)
            row["score"] = round(score, 3)
            rows.append(row)
        return rows

    def expand(
        self,
        namespace_id: str,
        *,
        surface: SurfaceKind | str | None,
        eligible_ids: Iterable[str],
    ) -> dict:
        if not self.allowed(namespace_id, surface):
            raise PermissionError("Capability namespace is outside the current interaction scope")
        node = self.node(namespace_id)
        eligible = set(eligible_ids)
        children = [
            self.row(child, surface=surface, eligible_ids=eligible)
            for child in self.children(node.id, surface)
        ]
        return {
            "namespace": self.row(node, surface=surface, eligible_ids=eligible),
            "children": children,
            "capability_ids": self.leaf_ids(node.id, eligible),
            "terminal": not children,
        }


DEFAULT_CAPABILITY_NAMESPACE_TREE = CapabilityNamespaceTree()
