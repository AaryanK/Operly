from __future__ import annotations

import re
from collections.abc import Iterable

from packages.kernel.contracts import CapabilityRisk, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.security.surfaces import capability_surface_allowed


class CapabilityRegistryError(RuntimeError):
    pass


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", str(value or "").lower())
        if len(token) > 1
    }


_RESOURCE_CONCEPTS = {
    "email": "mail",
    "emails": "mail",
    "mail": "mail",
    "gmail": "mail",
    "mailbox": "mail",
    "inbox": "mail",
    "calendar": "calendar",
    "calendars": "calendar",
    "event": "calendar",
    "events": "calendar",
    "meeting": "calendar",
    "meetings": "calendar",
    "schedule": "calendar",
    "schedules": "calendar",
    "file": "file",
    "files": "file",
    "document": "file",
    "documents": "file",
    "attachment": "file",
    "attachments": "file",
    "artifact": "file",
    "artifacts": "file",
    "workflow": "workflow",
    "workflows": "workflow",
}

_OPERATION_CONCEPTS = {
    "retrieve": "read",
    "retrieval": "read",
    "read": "read",
    "search": "read",
    "find": "read",
    "lookup": "read",
    "list": "read",
    "get": "read",
    "inspect": "read",
    "show": "read",
    "create": "create",
    "add": "create",
    "draft": "draft",
    "send": "send",
    "update": "update",
    "edit": "update",
    "modify": "update",
    "delete": "delete",
    "remove": "delete",
    "execute": "execute",
    "run": "execute",
    "wait": "wait",
    "watch": "wait",
    "monitor": "wait",
}


def _semantic_tokens(value: str) -> set[str]:
    raw = _tokens(value)
    expanded = set(raw)
    for token in raw:
        resource = _RESOURCE_CONCEPTS.get(token)
        if resource:
            expanded.add(resource)
        operation = _OPERATION_CONCEPTS.get(token)
        if operation:
            expanded.add(operation)
    return expanded


def _query_sections(query_text: str) -> tuple[str, set[str], set[str]]:
    """Parse the compact ObjectiveIR capability query without requiring it.

    Runtime 1.0 emits `objective | resources ... | operations ...`. Other callers may
    pass ordinary text, which still uses the normal lexical/semantic ranking path.
    """

    objective = query_text
    resources: set[str] = set()
    operations: set[str] = set()
    parts = [part.strip() for part in query_text.split("|") if part.strip()]
    if parts:
        objective = parts[0]
    for part in parts[1:]:
        lowered = part.lower()
        if lowered.startswith("resources "):
            resources.update(_semantic_tokens(part[len("resources ") :]))
        elif lowered.startswith("operations "):
            operations.update(_semantic_tokens(part[len("operations ") :]))
    return objective, resources, operations


class CapabilityRegistry:
    """Single source of truth for model/API visible capability contracts."""

    def __init__(self, specs: Iterable[CapabilitySpec] = ()) -> None:
        self._specs: dict[str, CapabilitySpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: CapabilitySpec) -> None:
        capability_id = spec.id.strip().lower()
        if not capability_id or capability_id != spec.id:
            raise CapabilityRegistryError("Capability IDs must be normalized lowercase names")
        if capability_id in self._specs:
            raise CapabilityRegistryError(f"Duplicate capability: {capability_id}")
        self._specs[capability_id] = spec

    def get(self, capability_id: str) -> CapabilitySpec:
        key = str(capability_id or "").strip().lower()
        try:
            return self._specs[key]
        except KeyError as error:
            raise CapabilityRegistryError(f"Unknown capability: {key or '<empty>'}") from error

    def all(self) -> tuple[CapabilitySpec, ...]:
        return tuple(self._specs[key] for key in sorted(self._specs))

    def visible(self, context: ExecutionContext) -> tuple[CapabilitySpec, ...]:
        scope = context.scope_kind.value
        return tuple(
            spec
            for spec in self.all()
            if scope in spec.scopes
            and capability_surface_allowed(spec.id, context.surface)
        )

    def effective(self, context: ExecutionContext) -> tuple[CapabilitySpec, ...]:
        return tuple(
            spec
            for spec in self.visible(context)
            if all(context.can(permission) for permission in spec.permissions)
        )

    def search(
        self,
        query: str,
        *,
        context: ExecutionContext,
        effective_only: bool = False,
        limit: int = 10,
    ) -> tuple[CapabilitySpec, ...]:
        candidates = self.effective(context) if effective_only else self.visible(context)
        query_text = str(query or "").strip().lower()
        if not query_text:
            return candidates[: max(1, min(limit, 50))]

        objective_text, resource_tokens, operation_tokens = _query_sections(query_text)
        query_tokens = _tokens(query_text)
        semantic_query_tokens = _semantic_tokens(query_text)
        objective_tokens = _tokens(objective_text)
        ranked: list[tuple[int, str, CapabilitySpec]] = []

        for spec in candidates:
            identity_text = " ".join((spec.id, spec.display_name, *spec.aliases, *spec.tags)).lower()
            joined = f"{identity_text} {spec.description}".lower()
            identity_tokens = _tokens(identity_text)
            description_tokens = _tokens(spec.description)
            semantic_spec_tokens = _semantic_tokens(joined)

            score = 0
            if query_text == spec.id:
                score += 1000
            if objective_text and objective_text in joined:
                score += 30
            elif query_text in joined:
                score += 20

            # Exact lexical matches still matter most for specific action/tool names.
            score += 10 * len(objective_tokens & identity_tokens)
            score += 6 * len((query_tokens - objective_tokens) & identity_tokens)
            score += 3 * len(query_tokens & description_tokens)

            # Small deterministic semantic normalization closes obvious vocabulary gaps
            # such as email/emails/Gmail/mailbox and retrieve/search/read. It is ranking
            # only: authority filtering has already happened above and Kernel rechecks it
            # again at execution.
            score += 4 * len(semantic_query_tokens & semantic_spec_tokens)

            if resource_tokens:
                score += 40 * len(resource_tokens & semantic_spec_tokens)
            if operation_tokens:
                score += 25 * len(operation_tokens & semantic_spec_tokens)
                if "read" in operation_tokens:
                    score += 15 if spec.risk is CapabilityRisk.READ_ONLY else -10

            if score > 0:
                ranked.append((score, spec.id, spec))

        ranked.sort(key=lambda row: (-row[0], row[1]))
        return tuple(row[2] for row in ranked[: max(1, min(limit, 50))])
