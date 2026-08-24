from __future__ import annotations

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.plugins import default_plugin_runtime


class EventDiscoveryProvider(BaseProvider):
    """Read-only discovery for plugin-declared Task trigger events.

    Events live on PluginManifest so adding a future plugin extends Task trigger
    discovery without teaching the Task engine vendor/domain-specific names.
    """

    name = "operly_event_discovery"
    capabilities = (
        CapabilityDefinition(
            "event.search",
            "event_search",
            "Search installed plugin events that can wake durable tasks. Use this before creating an event-triggered task instead of inventing event names.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 500},
                    "scope": {"type": "string", "enum": ["workspace", "personal", "either"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("workspace:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="events",
            tags=frozenset({"events", "tasks", "workflow", "discovery"}),
            semantic_operations=frozenset({"discover task triggers", "search plugin events"}),
        ),
        CapabilityDefinition(
            "event.describe",
            "event_describe",
            "Describe one installed plugin event, including its payload schema and scope, before binding a durable task to it.",
            {
                "type": "object",
                "properties": {"event_id": {"type": "string", "minLength": 1, "maxLength": 200}},
                "required": ["event_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("workspace:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="events",
            tags=frozenset({"events", "tasks", "workflow", "discovery"}),
        ),
    )

    @staticmethod
    def _rows(query: str = "", scope: str = "either") -> list[dict]:
        needle = " ".join(str(query or "").lower().split())
        wanted_scope = str(scope or "either").lower()
        rows: list[dict] = []
        for plugin_id, event in default_plugin_runtime().manifests.events():
            if wanted_scope != "either" and event.scope not in {wanted_scope, "either"}:
                continue
            document = " ".join(
                [event.id, event.description, plugin_id, " ".join(sorted(event.tags))]
            ).lower()
            if needle and all(token not in document for token in needle.split()):
                continue
            rows.append(
                {
                    "id": event.id,
                    "plugin_id": plugin_id,
                    "description": event.description,
                    "scope": event.scope,
                    "tags": sorted(event.tags),
                    "payload_schema": event.payload_schema,
                }
            )
        rows.sort(key=lambda row: (row["plugin_id"], row["id"]))
        return rows

    async def execute(self, context, capability_name, arguments):
        if capability_name == "event.search":
            limit = max(1, min(int(arguments.get("limit") or 12), 50))
            rows = self._rows(arguments.get("query") or "", arguments.get("scope") or "either")
            return CapabilityResult(True, False, {"events": rows[:limit], "count": min(len(rows), limit)})
        if capability_name == "event.describe":
            event_id = str(arguments.get("event_id") or "").strip()
            rows = [row for row in self._rows() if row["id"] == event_id]
            if not rows:
                return CapabilityResult(False, False, {"reason": "event_not_registered"})
            return CapabilityResult(True, False, {"event": rows[0]})
        return CapabilityResult(False, False, {"reason": "unsupported_event_discovery_capability"})
