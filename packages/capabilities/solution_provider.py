from sqlalchemy import select

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.software_projects.planning.plan_service import create_plan
from packages.database.custom_software_models import SoftwarePlanRecord
from packages.database.product_models import SolutionRecord
from packages.solutions.composer import create_solution_from_intent
from packages.solutions.service import SolutionService, solution_json


class UnifiedSolutionProvider(BaseProvider):
    """Canonical AI surface over Operly's solution lifecycle.

    Models describe the desired outcome. Operly decomposes that objective into a
    Solution capability manifest and keeps runtime selection behind the Solution
    boundary rather than asking the model to choose Studio/app/generated-project.
    """

    name = "operly_solutions"
    capabilities = (
        CapabilityDefinition(
            "solution.inspect",
            "solution_inspect",
            "Inspect all solutions available to this tenant across supported runtimes.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("solution:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "solution.compose",
            "solution_compose",
            "Create a reviewable working Solution from an owner objective. Operly derives the required surfaces, state, auth, workflows, jobs, notifications, and other primitives before choosing a compatibility runtime.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 200},
                    "objective": {"type": "string", "maxLength": 8000},
                },
                "required": ["name", "objective"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("solution:generate",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "solution.generate",
            "solution_generate",
            "Create a reviewable software plan for a missing business capability. Planning does not deploy or execute generated software.",
            {
                "type": "object",
                "properties": {"requirement": {"type": "string"}},
                "required": ["requirement"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("solution:generate",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "solution.create_digital_presence",
            "solution_create_digital_presence",
            "Create one draft Digital Presence from confirmed company context without repeating known facts.",
            {
                "type": "object",
                "properties": {"name": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("solution:generate",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
    )

    def __init__(self):
        self.service = SolutionService()

    async def execute(self, context, capability_name, arguments):
        if capability_name == "solution.inspect":
            rows = await self.service.list(context.db, context.tenant_id)
            return CapabilityResult(
                True,
                False,
                {"solutions": [solution_json(row) for row in rows]},
            )

        if capability_name == "solution.compose":
            if not context.actor_id:
                return CapabilityResult(False, False, {"reason": "authenticated_actor_required"})
            try:
                row, decision = await create_solution_from_intent(
                    context.db,
                    tenant_id=context.tenant_id,
                    user_id=context.actor_id,
                    name=str(arguments.get("name") or "").strip(),
                    objective=str(arguments.get("objective") or "").strip(),
                    service=self.service,
                )
            except ValueError as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            return CapabilityResult(
                True,
                True,
                {
                    "solution": solution_json(row),
                    "classification": decision.as_dict(),
                    "architecture_url": f"/api/solutions/{row.id}/architecture",
                },
                row.id,
            )

        if capability_name == "solution.create_digital_presence":
            if not context.actor_id:
                return CapabilityResult(False, False, {"reason": "authenticated_actor_required"})
            try:
                row = await self.service.create_presence(
                    context.db,
                    context.tenant_id,
                    context.actor_id,
                    arguments.get("name"),
                )
            except ValueError as error:
                return CapabilityResult(False, False, {"reason": str(error)})
            return CapabilityResult(True, True, solution_json(row), row.id)

        if capability_name == "solution.generate":
            if not context.actor_id:
                return CapabilityResult(False, False, {"reason": "authenticated_actor_required"})
            requirement = str(arguments["requirement"]).strip()[:12000]
            if not requirement:
                return CapabilityResult(False, False, {"reason": "requirement is required"})
            row, _, _ = await create_plan(
                context.db,
                context.tenant_id,
                context.actor_id,
                requirement,
            )
            return CapabilityResult(
                True,
                True,
                {
                    "plan_id": row.id,
                    "status": row.status,
                    "next_step": "owner_review_and_approval",
                },
                row.id,
            )

        return CapabilityResult(False, False, {"reason": "unsupported_solution_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if not result.success:
            return CapabilityResult(False, result.changed, result.evidence, result.external_reference)
        if capability_name == "solution.inspect":
            return CapabilityResult(True, False, {"inventory_observed": True, **result.evidence})
        if not result.external_reference:
            return CapabilityResult(False, result.changed, {"reason": "verification_target_missing"})

        model = SoftwarePlanRecord if capability_name == "solution.generate" else SolutionRecord
        row = await context.db.scalar(
            select(model).where(
                model.id == result.external_reference,
                model.tenant_id == context.tenant_id,
            )
        )
        return CapabilityResult(
            row is not None,
            result.changed,
            {"persisted": row is not None, **result.evidence},
            result.external_reference,
        )
