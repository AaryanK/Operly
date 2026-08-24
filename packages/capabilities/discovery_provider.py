"""Permanent capability-discovery kernel exposed to capable agents.

Discovery returns metadata and schemas only. It never invokes the discovered
capability and never upgrades the caller's authority.
"""
from __future__ import annotations

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.capabilities.search_index import CapabilitySearchIndex
from packages.security.surfaces import SurfaceKind, capability_surface_allowed


class CapabilityDiscoveryProvider(BaseProvider):
    name = "operly_capability_discovery"
    capabilities = (
        CapabilityDefinition(
            "capability.search",
            "capability_search",
            "Semantically search capabilities eligible for this authenticated surface. Returns metadata only and never grants permission.",
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
        self.search_index = CapabilitySearchIndex()

    @staticmethod
    def _surface(context) -> SurfaceKind:
        invocation = context.invocation or {}
        return SurfaceKind.coerce(
            invocation.get("surface")
            or (invocation.get("metadata") or {}).get("_surface_kind")
        )

    def _eligible_definitions(self, context, authority: set[str]):
        surface = self._surface(context)
        return [
            definition
            for definition in self.registry.metadata(context.tenant_id, authority=authority)
            if capability_surface_allowed(definition.id, surface)
        ]

    async def execute(self, context, capability_name, arguments):
        authority = set((context.invocation or {}).get("authority") or [])
        surface = self._surface(context)
        eligible = self._eligible_definitions(context, authority)
        eligible_by_id = {definition.id: definition for definition in eligible}

        if capability_name == "capability.search":
            hits = self.search_index.search(
                eligible,
                str(arguments.get("query") or ""),
                limit=int(arguments.get("limit") or 8),
                categories=arguments.get("categories") or (),
                tags=arguments.get("tags") or (),
            )
            rows = []
            for hit in hits:
                definition = eligible_by_id.get(hit.capability_id)
                if definition is None:
                    continue
                descriptor = self.registry.descriptor(
                    context.tenant_id,
                    definition.id,
                    authority=authority,
                )
                availability = self.registry.availability(
                    context.tenant_id,
                    definition.id,
                    authority=authority,
                )
                rows.append(
                    {
                        "id": descriptor.id,
                        "version": descriptor.version,
                        "plugin_id": descriptor.plugin_id,
                        "display_name": descriptor.display_name,
                        "description": descriptor.description,
                        "risk": descriptor.risk,
                        "category": descriptor.category,
                        "tags": list(descriptor.tags),
                        "semantic_operations": list(descriptor.semantic_operations),
                        "installed": descriptor.installed,
                        "configured": descriptor.configured,
                        "healthy": descriptor.healthy,
                        "authorized": True,
                        "availability": availability.as_dict(),
                        "score": hit.score,
                        "semantic_score": hit.semantic_score,
                        "lexical_score": hit.lexical_score,
                    }
                )
            return CapabilityResult(
                True,
                False,
                {
                    "capabilities": rows,
                    "count": len(rows),
                    "eligible_count": len(eligible),
                    "surface": surface.value,
                    "schemas_included": False,
                    "note": "Search ranks only an already-authorized surface-visible candidate set; discovery is not execution authority.",
                },
            )

        if capability_name == "capability.describe":
            requested = [str(item) for item in arguments.get("ids") or []]
            visible_ids = [item for item in requested if item in eligible_by_id]
            rows = self.registry.describe(
                context.tenant_id,
                visible_ids,
                authority=authority,
                include_schema=True,
            )
            return CapabilityResult(
                True,
                False,
                {
                    "capabilities": rows,
                    "count": len(rows),
                    "requested_count": len(requested),
                    "surface": surface.value,
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
