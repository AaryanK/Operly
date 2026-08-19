from packages.business_brain.operations_service import get_operations_service
from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider


class OperationsProvider(BaseProvider):
    name = "operly_operations"
    capabilities = (
        CapabilityDefinition(
            "operations.brief",
            "operations_brief",
            "Read the owner's prioritized operational brief.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("operations:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "operations.scan",
            "operations_scan",
            "Scan tenant-scoped business data for operational exceptions.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="medium",
            permissions=("operations:write",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "operations.audit",
            "operations_audit",
            "Create a new internal business health audit.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="medium",
            permissions=("operations:write",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "operations.generate_plan",
            "operations_generate_plan",
            "Generate a draft visual operating plan from a business goal.",
            {
                "type": "object",
                "properties": {"goal": {"type": "string"}},
                "required": ["goal"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("operations:write",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
    )

    async def execute(self, context, capability_name, arguments):
        service = get_operations_service()
        if capability_name == "operations.brief":
            result = await service.operational_brief(context.tenant_id, context.actor_id or "")
            return CapabilityResult(True, False, result)
        if capability_name == "operations.scan":
            alerts = await service.run_operational_scan(context.tenant_id, str(context.actor_id or "OPERLY"))
            return CapabilityResult(True, True, {"active_alerts": alerts})
        if capability_name == "operations.audit":
            result = await service.run_audit(context.tenant_id, context.actor_id or "")
            return CapabilityResult(True, True, {"audit": result})
        if capability_name == "operations.generate_plan":
            result = await service.generate_plan(
                context.tenant_id,
                context.actor_id or "",
                str(arguments["goal"]).strip(),
            )
            return CapabilityResult(True, True, {"plan": result})
        return CapabilityResult(False, False, {"reason": "unsupported_operations_capability"})

    async def verify(self, context, capability_name, arguments, result):
        return CapabilityResult(result.success, result.changed, {"completed": result.success, **result.evidence})
