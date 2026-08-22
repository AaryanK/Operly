"""Permanent capability-discovery kernel exposed to capable agents.

Discovery returns metadata and schemas only. It never invokes the discovered
capability and never upgrades the caller's authority.
"""
from __future__ import annotations

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider


class CapabilityDiscoveryProvider(BaseProvider):
    name = "operly_capability_discovery"
    capabilities = (
        CapabilityDefinition(
            "capability.search",
            "capability_search",
            "Search installed Operly capabilities by the operation you need. Discovery does not grant permission to execute a result.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 1000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 10,
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 10,
                    },
                },
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="operly.core.discovery",
            category="discovery",
            tags=frozenset({"kernel", "discovery"}),
            semantic_operations=frozenset({"find tool", "find capability", "discover operation"}),
        ),
        CapabilityDefinition(
            "capability.describe",
            "capability_describe",
            "Return exact schemas and availability metadata for discovered capability IDs. This does not execute them.",
            {
                "type": "object",
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 12,
                    }
                },
                "required": ["ids"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="operly.core.discovery",
            category="discovery",
            tags=frozenset({"kernel", "discovery"}),
            semantic_operations=frozenset({"describe tool", "describe capability", "get tool schema"}),
        ),
    )

    def __init__(self, registry) -> None:
        self.registry = registry

    async def execute(self, context, capability_name, arguments):
        authority = set((context.invocation or {}).get("authority") or [])
        if capability_name == "capability.search":
            rows = self.registry.search(
                context.tenant_id,
                str(arguments.get("query") or ""),
                authority=authority,
                limit=int(arguments.get("limit") or 12),
                categories=arguments.get("categories") or (),
                tags=arguments.get("tags") or (),
            )
            return CapabilityResult(
                True,
                False,
                {
                    "capabilities": rows,
                    "count": len(rows),
                    "note": "Discovery metadata is not execution authority.",
                },
            )
        if capability_name == "capability.describe":
            rows = self.registry.describe(
                context.tenant_id,
                [str(item) for item in arguments.get("ids") or []],
                authority=authority,
                include_schema=True,
            )
            return CapabilityResult(
                True,
                False,
                {
                    "capabilities": rows,
                    "count": len(rows),
                    "note": "Invoke selected capabilities through the normal Operly capability boundary.",
                },
            )
        return CapabilityResult(False, False, {"reason": "unsupported_discovery_capability"})

    async def verify(self, context, capability_name, arguments, result):
        return CapabilityResult(
            result.success,
            False,
            {"metadata_only": True, **result.evidence},
            result.external_reference,
        )
