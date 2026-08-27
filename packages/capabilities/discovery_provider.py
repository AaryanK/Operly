"""Permanent, scope-aware capability discovery kernel.

The model navigates a small namespace tree instead of ranking every registered leaf
capability against every request.  Namespace routing is model-facing only; execution
still crosses the canonical registry, authority checks, firewall, approvals, audit,
and verification boundary.
"""
from __future__ import annotations

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.namespaces import DEFAULT_CAPABILITY_NAMESPACE_TREE
from packages.capabilities.providers import BaseProvider
from packages.channels.presentation import connector_tool_context
from packages.security.surfaces import SurfaceKind, capability_surface_allowed


class CapabilityDiscoveryProvider(BaseProvider):
    name = "operly_capability_discovery"
    capabilities = (
        CapabilityDefinition(
            "capability.search",
            "capability_search",
            "Search the small capability namespace available on this authenticated surface. Returns namespace paths only, never executable schemas. Choose the best domain, then call capability.expand.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 1000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 12},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="operly.core.discovery",
            category="discovery",
            tags=frozenset({"kernel", "discovery", "namespace"}),
            semantic_operations=frozenset({"find capability domain", "search capability namespace"}),
        ),
        CapabilityDefinition(
            "capability.expand",
            "capability_expand",
            "Expand one capability namespace under the current trusted scope. Returns immediate child namespaces and only the governed operation IDs mounted directly at this node. It never returns schemas or grants authority.",
            {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "minLength": 1, "maxLength": 200}
                },
                "required": ["namespace"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="operly.core.discovery",
            category="discovery",
            tags=frozenset({"kernel", "discovery", "namespace"}),
            semantic_operations=frozenset({"expand capability domain", "browse capability namespace"}),
        ),
        CapabilityDefinition(
            "capability.describe",
            "capability_describe",
            "Return exact schemas for operation IDs mounted directly under one namespace that is allowed on this surface. IDs outside that namespace are rejected. This does not execute them.",
            {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "minLength": 1, "maxLength": 200},
                    "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12},
                },
                "required": ["namespace", "ids"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="operly.core.discovery",
            category="discovery",
            tags=frozenset({"kernel", "discovery", "schema"}),
            semantic_operations=frozenset({"describe capability operation", "get exact operation schema"}),
        ),
        # Retained for compatibility with explicit transport/presentation callers. It
        # is intentionally not part of the permanent model kernel or namespace tree.
        CapabilityDefinition(
            "connector.presentation",
            "connector_presentation",
            "Return the last-mile formatting/transport contract for a connector. This describes presentation only and grants no connector authority.",
            {
                "type": "object",
                "properties": {
                    "provider": {"type": "string", "maxLength": 80}
                },
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            approval_policy=ApprovalPolicy.AUTO,
            plugin_id="operly.core.discovery",
            category="connector",
        ),
    )

    def __init__(self, registry) -> None:
        self.registry = registry
        self.namespaces = DEFAULT_CAPABILITY_NAMESPACE_TREE

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

    def _eligible_ids(self, context, authority: set[str]) -> set[str]:
        return {definition.id for definition in self._eligible_definitions(context, authority)}

    async def execute(self, context, capability_name, arguments):
        invocation = context.invocation or {}
        authority = set(invocation.get("authority") or [])
        surface = self._surface(context)
        root = self.namespaces.root_for(surface)

        if capability_name == "connector.presentation":
            metadata = invocation.get("metadata") if isinstance(invocation.get("metadata"), dict) else {}
            provider = str(
                arguments.get("provider")
                or metadata.get("origin_provider")
                or invocation.get("channel")
                or "web"
            ).strip().lower()
            return CapabilityResult(
                True,
                False,
                {
                    "presentation": connector_tool_context(provider),
                    "surface": surface.value,
                    "authorization_granted": False,
                },
            )

        eligible_ids = self._eligible_ids(context, authority)

        if capability_name == "capability.search":
            rows = self.namespaces.search(
                str(arguments.get("query") or ""),
                surface=surface,
                eligible_ids=eligible_ids,
                limit=int(arguments.get("limit") or 8),
            )
            ranked = [row["id"] for row in rows]
            return CapabilityResult(
                True,
                False,
                {
                    "root": root,
                    "surface": surface.value,
                    "namespaces": rows,
                    "count": len(rows),
                    "ranked_namespace_ids": ranked,
                    "schemas_included": False,
                    "next_action": (
                        "Call capability.expand on the most relevant namespace. Continue expanding until the needed operation IDs are mounted at that node; then call capability.describe with that same namespace."
                        if rows
                        else "No matching namespace is currently mounted under this interaction scope."
                    ),
                    "note": "Namespace discovery does not grant execution authority and cannot cross the trusted surface root.",
                },
            )

        if capability_name == "capability.expand":
            namespace_id = str(arguments.get("namespace") or "").strip().lower()
            try:
                expansion = self.namespaces.expand(
                    namespace_id,
                    surface=surface,
                    eligible_ids=eligible_ids,
                )
            except (LookupError, PermissionError) as error:
                return CapabilityResult(
                    False,
                    False,
                    {
                        "reason": "namespace_unavailable",
                        "detail": str(error),
                        "root": root,
                        "surface": surface.value,
                    },
                )
            expansion.update(
                {
                    "root": root,
                    "surface": surface.value,
                    "schemas_included": False,
                    "next_action": (
                        "Call capability.describe with this namespace and only the needed capability_ids."
                        if expansion.get("capability_ids")
                        else "Expand the relevant child namespace."
                    ),
                }
            )
            return CapabilityResult(True, False, expansion)

        if capability_name == "capability.describe":
            namespace_id = str(arguments.get("namespace") or "").strip().lower()
            requested = [str(item).strip() for item in arguments.get("ids") or [] if str(item).strip()]
            try:
                if not self.namespaces.allowed(namespace_id, surface):
                    raise PermissionError("Namespace is outside the current interaction scope")
                mounted = set(self.namespaces.leaf_ids(namespace_id, eligible_ids))
            except (LookupError, PermissionError) as error:
                return CapabilityResult(
                    False,
                    False,
                    {
                        "reason": "namespace_unavailable",
                        "detail": str(error),
                        "root": root,
                        "surface": surface.value,
                    },
                )

            rejected = [item for item in requested if item not in mounted]
            if rejected:
                return CapabilityResult(
                    False,
                    False,
                    {
                        "reason": "capability_not_mounted_in_namespace",
                        "namespace": namespace_id,
                        "rejected_ids": rejected,
                        "mounted_ids": sorted(mounted),
                        "surface": surface.value,
                    },
                )

            rows = self.registry.describe(
                context.tenant_id,
                requested,
                authority=authority,
                include_schema=True,
            )
            return CapabilityResult(
                True,
                False,
                {
                    "namespace": namespace_id,
                    "capabilities": rows,
                    "count": len(rows),
                    "requested_count": len(requested),
                    "surface": surface.value,
                    "note": "Invoke the selected operation through the normal governed Operly capability boundary.",
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
