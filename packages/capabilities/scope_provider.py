from __future__ import annotations

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.security.scope_resolver import authorized_scopes, resolve_authorized_scope


class AccountScopeProvider(BaseProvider):
    """Resolve Personal/workspace namespaces without granting execution authority."""

    name = "operly_account_scopes"
    capabilities = (
        CapabilityDefinition(
            "scope.list",
            "scope_list",
            "List the Personal scope and every workspace the authenticated human may resolve.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("workspace:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="account",
        ),
        CapabilityDefinition(
            "scope.resolve",
            "scope_resolve",
            "Resolve an explicit Personal/workspace reference inside the authenticated human's authorized scope inventory. This returns a target namespace only; it grants no capability authority.",
            {
                "type": "object",
                "properties": {
                    "reference": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200,
                        "description": "Explicit scope reference such as Personal, ANHITRA, or NaySchool.",
                    }
                },
                "required": ["reference"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("workspace:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="account",
        ),
    )

    async def execute(self, context, capability_name, arguments):
        if not context.actor_id:
            return CapabilityResult(False, False, {"reason": "authenticated_user_required"})

        if capability_name == "scope.list":
            scopes = await authorized_scopes(context.db, user_id=context.actor_id)
            return CapabilityResult(
                True,
                False,
                {"scopes": [item.as_dict() for item in scopes], "count": len(scopes)},
            )

        if capability_name == "scope.resolve":
            metadata = ((context.invocation or {}).get("metadata") or {})
            resolution = await resolve_authorized_scope(
                context.db,
                user_id=context.actor_id,
                reference=str(arguments.get("reference") or ""),
                focus_workspace_id=(
                    str(metadata.get("focus_workspace_id") or "").strip() or None
                ),
            )
            payload = resolution.as_dict()
            return CapabilityResult(
                resolution.status == "resolved",
                False,
                payload,
            )

        return CapabilityResult(False, False, {"reason": "unsupported_scope_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if capability_name == "scope.list":
            valid = result.success and isinstance(result.evidence.get("scopes"), list)
            return CapabilityResult(valid, False, result.evidence)
        if capability_name == "scope.resolve":
            valid = result.success and result.evidence.get("status") == "resolved" and bool(
                result.evidence.get("scope")
            )
            return CapabilityResult(valid, False, result.evidence)
        return CapabilityResult(False, False, {"reason": "unsupported_scope_capability"})
