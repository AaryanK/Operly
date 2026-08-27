"""Scope-aware hierarchical namespace for model-facing capability discovery.

The canonical CapabilityRegistry remains the execution/security catalog.  This module
is deliberately smaller: it is the navigation tree presented to a model.  A model
chooses a domain first, expands that domain, and only terminal nodes reveal governed
capability IDs whose exact schemas may then be described.

Namespace membership never grants authority.  Callers must intersect terminal leaves
with the registry's already-authorized/surface-visible capability set.
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
        return " ".join(
            (
                self.id,
                self.label,
                self.description,
                *self.aliases,
            )
        ).lower()


# Keep this deliberately explicit.  New product areas should be mounted here one at
# a time rather than becoming globally searchable merely because a provider happens
# to register another CapabilityDefinition.
_NODES = (
    CapabilityNamespaceNode(
        "user",
        "User",
        "The authenticated person's private Operly account surface.",
        None,
        ("personal", "account", "me"),
    ),
    CapabilityNamespaceNode(
        "user.settings",
        "Settings",
        "Private user-level preferences and account settings.",
        "user",
        ("preferences", "profile settings"),
    ),
    CapabilityNamespaceNode(
        "user.connections",
        "Connections",
        "Private services and accounts connected by the user.",
        "user",
        ("connectors", "integrations", "linked accounts"),
        ("account.list_personal_connectors",),
    ),
    CapabilityNamespaceNode(
        "user.connections.google",
        "Google",
        "The user's private Google connection.",
        "user.connections",
        ("google account",),
    ),
    CapabilityNamespaceNode(
        "user.connections.google.gmail",
        "Gmail",
        "Read and manage the user's private Gmail mailbox.",
        "user.connections.google",
        ("email", "mail", "inbox", "draft"),
        (
            "gmail.search",
            "gmail.read_message",
            "gmail.read_thread",
            "gmail.list_attachments",
            "gmail.read_attachment",
            "gmail.create_draft",
            "gmail.create_draft_with_artifacts",
            "gmail.list_drafts",
            "gmail.get_draft",
            "gmail.update_draft",
            "gmail.send_draft",
            "gmail.delete_draft",
            "gmail.modify_labels",
            "gmail.send_email",
        ),
    ),
    CapabilityNamespaceNode(
        "user.connections.google.calendar",
        "Google Calendar",
        "Read and manage the user's private Google Calendar.",
        "user.connections.google",
        ("calendar", "schedule", "meeting", "availability"),
        (
            "calendar.list_events",
            "calendar.list_calendars",
            "calendar.freebusy",
            "calendar.assess_deadline_conflicts",
            "calendar.create_event",
            "calendar.update_event",
            "calendar.delete_event",
        ),
    ),
    CapabilityNamespaceNode(
        "user.workspaces",
        "Workspaces",
        "Workspaces the authenticated user belongs to and explicit delegation into one of them.",
        "user",
        ("servers", "tenants", "businesses", "organizations"),
        (
            "account.list_workspaces",
            "account.create_workspace",
            "account.update_workspace",
            "account.workspace_overview",
            "account.workspace_capabilities",
            "account.workspace_execute",
        ),
    ),

    CapabilityNamespaceNode(
        "workspace",
        "Workspace",
        "The currently authorized Operly workspace. Personal account branches are not visible here.",
        None,
        ("business", "tenant", "organization"),
    ),
    CapabilityNamespaceNode(
        "workspace.crm",
        "CRM",
        "Workspace customer, contact, lead, and pipeline operations.",
        "workspace",
        ("customer", "contact", "lead", "pipeline", "sales"),
    ),
    CapabilityNamespaceNode(
        "workspace.operations",
        "Operations",
        "Workspace operational work and business processes.",
        "workspace",
        ("tasks", "orders", "inventory", "workflow", "business operations"),
    ),
    CapabilityNamespaceNode(
        "workspace.activity",
        "Activity",
        "Workspace action, event, and activity history.",
        "workspace",
        ("events", "audit", "history", "actions"),
    ),
    CapabilityNamespaceNode(
        "workspace.presence",
        "Presence",
        "Workspace presence and real-time participant state.",
        "workspace",
        ("online", "status", "participants"),
    ),
    CapabilityNamespaceNode(
        "workspace.solutions",
        "Solutions",
        "Software and solutions owned by the current workspace.",
        "workspace",
        ("apps", "applications", "websites", "software"),
    ),
    CapabilityNamespaceNode(
        "workspace.solutions.studio",
        "Studio",
        "Build, edit, inspect, and export canonical Operly software projects.",
        "workspace.solutions",
        ("software studio", "website studio", "app builder", "code"),
    ),
    CapabilityNamespaceNode(
        "workspace.solutions.studio.projects",
        "Projects",
        "Create, list, and inspect canonical Studio software projects.",
        "workspace.solutions.studio",
        ("project", "solution project"),
        (
            "software.project.list",
            "software.project.create",
            "software.project.inspect",
        ),
    ),
    CapabilityNamespaceNode(
        "workspace.solutions.studio.build",
        "Build",
        "Build or edit software, inspect durable build progress, and export canonical source.",
        "workspace.solutions.studio",
        ("generate app", "build app", "edit app", "source", "build status"),
        (
            "software.build",
            "software.edit",
            "software.build.status",
            "software.source.export",
        ),
    ),
    CapabilityNamespaceNode(
        "workspace.solutions.studio.bindings",
        "Bindings",
        "Inspect or configure governed service bindings for a software project.",
        "workspace.solutions.studio",
        ("service binding", "project integration"),
        (
            "software.binding.list",
            "software.binding.create",
            "software.binding.revoke",
        ),
    ),
    CapabilityNamespaceNode(
        "workspace.connections",
        "Connections",
        "External services connected to the current workspace.",
        "workspace",
        ("connectors", "integrations", "linked services"),
    ),
    CapabilityNamespaceNode(
        "workspace.connections.google",
        "Google",
        "The Google connection authorized for this workspace.",
        "workspace.connections",
        ("google workspace",),
    ),
    CapabilityNamespaceNode(
        "workspace.connections.google.gmail",
        "Gmail",
        "Read and manage Gmail through the Google connection authorized for this workspace.",
        "workspace.connections.google",
        ("email", "mail", "inbox", "draft"),
        (
            "gmail.search",
            "gmail.read_message",
            "gmail.read_thread",
            "gmail.list_attachments",
            "gmail.read_attachment",
            "gmail.create_draft",
            "gmail.create_draft_with_artifacts",
            "gmail.list_drafts",
            "gmail.get_draft",
            "gmail.update_draft",
            "gmail.send_draft",
            "gmail.delete_draft",
            "gmail.modify_labels",
            "gmail.send_email",
        ),
    ),
    CapabilityNamespaceNode(
        "workspace.connections.google.calendar",
        "Google Calendar",
        "Read and manage Google Calendar through the connection authorized for this workspace.",
        "workspace.connections.google",
        ("calendar", "schedule", "meeting", "availability"),
        (
            "calendar.list_events",
            "calendar.list_calendars",
            "calendar.freebusy",
            "calendar.assess_deadline_conflicts",
            "calendar.create_event",
            "calendar.update_event",
            "calendar.delete_event",
        ),
    ),
    CapabilityNamespaceNode(
        "workspace.plugins",
        "Plugins",
        "Plugins installed or configured for the current workspace.",
        "workspace",
        ("extensions", "capabilities"),
    ),
    CapabilityNamespaceNode(
        "workspace.members_roles",
        "Members and Roles",
        "Workspace members, invitations, roles, and role permissions.",
        "workspace",
        ("members", "roles", "permissions", "invite", "access"),
    ),
    CapabilityNamespaceNode(
        "workspace.ai",
        "AI",
        "Workspace AI configuration and agent behavior.",
        "workspace",
        ("agent", "model", "assistant"),
    ),
    CapabilityNamespaceNode(
        "workspace.mcp",
        "MCP",
        "Workspace MCP clients and capability access configuration.",
        "workspace",
        ("model context protocol", "client"),
    ),
    CapabilityNamespaceNode(
        "workspace.settings",
        "Settings",
        "Workspace-level settings and presentation configuration.",
        "workspace",
        ("preferences", "configuration", "timezone"),
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
        kind = SurfaceKind.coerce(surface)
        if kind.allows_personal_global:
            return "user"
        return "workspace"

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

    def children(self, namespace_id: str, surface: SurfaceKind | str | None) -> list[CapabilityNamespaceNode]:
        if not self.allowed(namespace_id, surface):
            return []
        return [self._nodes[item] for item in self._children.get(namespace_id, ())]

    def leaf_ids(self, namespace_id: str, eligible_ids: Iterable[str]) -> list[str]:
        eligible = set(eligible_ids)
        node = self.node(namespace_id)
        return [capability_id for capability_id in node.capability_ids if capability_id in eligible]

    def descendant_leaf_ids(self, namespace_id: str, eligible_ids: Iterable[str]) -> set[str]:
        eligible = set(eligible_ids)
        output: set[str] = set(self.leaf_ids(namespace_id, eligible))
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
        children = self.children(node.id, surface)
        direct = self.leaf_ids(node.id, eligible_ids)
        descendant = self.descendant_leaf_ids(node.id, eligible_ids)
        return {
            "id": node.id,
            "label": node.label,
            "description": node.description,
            "depth": node.depth,
            "parent_id": node.parent_id,
            "child_count": len(children),
            "operation_count": len(descendant),
            "terminal": not children,
            "available_here": len(direct),
        }

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {item for item in re.findall(r"[a-z0-9]+", str(value or "").lower()) if len(item) > 1}

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
            # Prefer a concrete branch that actually owns currently eligible leaves,
            # but keep empty conceptual branches searchable for transparent "not yet
            # available" answers and incremental product expansion.
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
        terminal = not children
        return {
            "namespace": self.row(node, surface=surface, eligible_ids=eligible),
            "children": children,
            "capability_ids": self.leaf_ids(node.id, eligible) if terminal else [],
            "terminal": terminal,
        }


DEFAULT_CAPABILITY_NAMESPACE_TREE = CapabilityNamespaceTree()
