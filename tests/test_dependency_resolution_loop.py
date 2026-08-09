import json
import unittest

from packages.custom_software.dependency_resolution import (
    DependencyResolutionOutput,
    dependency_findings,
    validate_dependency_resolution,
)
from packages.custom_software.live_planning import (
    FailureClass,
    PlanningBlocked,
    PlanningBudget,
    ProposedNode,
    RequirementsAnalysis,
    StructuredModelResult,
    ValidatorOutput,
)
from packages.custom_software.planning_orchestrator import RecursiveRepairPlanningOrchestrator


def node(node_id, responsibility, **values):
    data = {
        "node_id": node_id,
        "title": node_id.replace("_", " "),
        "node_type": "domain_engine",
        "objective": responsibility,
        "responsibilities": [responsibility],
        "linked_requirement_ids": ["R-001"],
        "inputs": ["request"],
        "outputs": ["result"],
        "dependencies": [],
        "state_effects": ["record state change"],
        "invariants": ["state remains internally consistent"],
        "failure_cases": ["reject invalid state transition"],
        "security_constraints": ["authorize the actor"],
        "persistence_behavior": ["persist durable state"],
        "required_artifacts": [f"{node_id} implementation"],
        "required_tests": [f"{node_id} happy path and invalid transition test"],
        "assumptions": [],
        "scope_claims": [],
        "children": [],
    }
    data.update(values)
    return data


class ScriptedModel:
    provider = "scripted"
    model_id = "fake-v1"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def generate_structured(
        self, *, role, context, output_schema, request_id, timeout_seconds, attempt=1
    ):
        self.calls.append((role, context))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            return StructuredModelResult(
                provider=self.provider,
                model_id=self.model_id,
                request_id=request_id,
                attempt=attempt,
                latency_ms=1,
                validation_errors=[str(value)],
                failure_classification=FailureClass.MALFORMED_OUTPUT,
                context_digest=context.digest(),
            )
        output = output_schema.model_validate(value).model_dump(mode="json")
        return StructuredModelResult(
            provider=self.provider,
            model_id=self.model_id,
            request_id=request_id,
            attempt=attempt,
            input_tokens=10,
            output_tokens=10,
            latency_ms=1,
            structured_output=output,
            raw_response=json.dumps(value),
            context_digest=context.digest(),
        )


ANALYST = {
    "root_objective": "Manage veterinary appointments",
    "requirements": [
        {
            "requirement_id": "R-001",
            "source_excerpt": "Build a veterinary appointment management system",
            "normalized_requirement": "Manage veterinary appointments",
            "category": "behavior",
            "priority": "mandatory",
            "acceptance_criteria": ["appointments can be managed end to end"],
        }
    ],
    "global_exclusions": [],
}

APPROVE = {
    "disposition": "approve",
    "ready_for_implementation": True,
    "semantic_coverage": "complete",
    "reasoning_summary": "ready",
}

MISSING_AVAILABILITY = {
    "disposition": "resolve_dependency",
    "ready_for_implementation": False,
    "semantic_coverage": "partial",
    "missing_dependencies": ["Veterinarian availability contract"],
    "reasoning_summary": "appointment assignment requires staff availability",
}

GLOBAL_OK = {
    "approved": True,
    "semantic_completeness": "complete",
    "reasoning_summary": "complete",
}


class DependencyResolutionUnitTests(unittest.TestCase):
    def test_dependency_resolution_must_cover_findings_and_stay_in_requirement_scope(self):
        analysis = RequirementsAnalysis.model_validate(ANALYST)
        blocked = ProposedNode.model_validate(node("appointments", "manage appointment state"))
        verdict = ValidatorOutput.model_validate(MISSING_AVAILABILITY)
        findings = dependency_findings(blocked, verdict)
        output = DependencyResolutionOutput.model_validate(
            {
                "resolutions": [
                    {
                        "finding_id": findings[0]["finding_id"],
                        "action": "create_dependency",
                        "dependency_node": node(
                            "availability",
                            "manage veterinary staff availability",
                            linked_requirement_ids=["R-999"],
                        ),
                        "rationale": "availability is required for appointment assignment",
                    }
                ]
            }
        )
        errors = validate_dependency_resolution(
            output,
            findings,
            blocked,
            [blocked],
            analysis,
            PlanningBudget(),
        )
        self.assertTrue(any("expanded beyond blocked node requirements" in item for item in errors))


class DependencyResolutionOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_dependency_is_created_validated_and_blocked_node_revalidated(self):
        appointments = node("appointments", "manage appointment state")
        availability = node("availability", "manage veterinary staff availability")
        verdict = ValidatorOutput.model_validate(MISSING_AVAILABILITY)
        finding_id = dependency_findings(ProposedNode.model_validate(appointments), verdict)[0]["finding_id"]
        resolution = {
            "resolutions": [
                {
                    "finding_id": finding_id,
                    "action": "create_dependency",
                    "dependency_node": availability,
                    "rationale": "availability is the smallest contract required before assigning appointments",
                }
            ]
        }
        client = ScriptedModel(
            [
                ANALYST,
                {"nodes": [appointments]},
                MISSING_AVAILABILITY,
                resolution,
                APPROVE,
                APPROVE,
                GLOBAL_OK,
            ]
        )
        orchestrator = RecursiveRepairPlanningOrchestrator(
            client, PlanningBudget(max_model_calls=20)
        )
        result = await orchestrator.run("Build a veterinary appointment management system")

        self.assertTrue(result["global"].approved)
        self.assertEqual({item.node_id for item in result["nodes"]}, {"appointments", "availability"})
        appointment = next(item for item in result["nodes"] if item.node_id == "appointments")
        self.assertEqual(appointment.dependencies, ["availability"])
        self.assertEqual(
            [role for role, _ in client.calls],
            [
                "requirements_analyst",
                "planner",
                "validator",
                "dependency_resolver",
                "validator",
                "validator",
                "global_validator",
            ],
        )
        self.assertEqual(orchestrator.dependency_work_items[-1]["state"], "resolved")
        self.assertEqual(
            orchestrator.dependency_resolution_traces[-1]["created_node_ids"],
            ["availability"],
        )

    async def test_existing_ready_node_is_linked_instead_of_duplicated(self):
        availability = node("availability", "manage veterinary staff availability")
        appointments = node("appointments", "manage appointment state")
        verdict = ValidatorOutput.model_validate(MISSING_AVAILABILITY)
        finding_id = dependency_findings(ProposedNode.model_validate(appointments), verdict)[0]["finding_id"]
        resolution = {
            "resolutions": [
                {
                    "finding_id": finding_id,
                    "action": "link_existing",
                    "existing_node_id": "availability",
                    "rationale": "the existing availability leaf already provides the required contract",
                }
            ]
        }
        client = ScriptedModel(
            [
                ANALYST,
                {"nodes": [availability, appointments]},
                APPROVE,
                MISSING_AVAILABILITY,
                resolution,
                APPROVE,
                GLOBAL_OK,
            ]
        )
        orchestrator = RecursiveRepairPlanningOrchestrator(
            client, PlanningBudget(max_model_calls=20)
        )
        result = await orchestrator.run("Build a veterinary appointment management system")

        self.assertTrue(result["global"].approved)
        self.assertEqual(len([x for x in result["nodes"] if x.node_id == "availability"]), 1)
        appointment = next(item for item in result["nodes"] if item.node_id == "appointments")
        self.assertEqual(appointment.dependencies, ["availability"])
        self.assertEqual(orchestrator.dependency_resolution_traces[-1]["created_node_ids"], [])

    async def test_repeated_unresolved_dependency_hits_bounded_attempt_limit(self):
        appointments = node("appointments", "manage appointment state")
        verdict = ValidatorOutput.model_validate(MISSING_AVAILABILITY)
        finding_id = dependency_findings(ProposedNode.model_validate(appointments), verdict)[0]["finding_id"]
        availability = node("availability", "manage veterinary staff availability")
        create = {
            "resolutions": [
                {
                    "finding_id": finding_id,
                    "action": "create_dependency",
                    "dependency_node": availability,
                    "rationale": "resolve availability",
                }
            ]
        }
        link = {
            "resolutions": [
                {
                    "finding_id": finding_id,
                    "action": "link_existing",
                    "existing_node_id": "availability",
                    "rationale": "reuse availability",
                }
            ]
        }
        client = ScriptedModel(
            [
                ANALYST,
                {"nodes": [appointments]},
                MISSING_AVAILABILITY,
                create,
                APPROVE,
                MISSING_AVAILABILITY,
                link,
                MISSING_AVAILABILITY,
                link,
                MISSING_AVAILABILITY,
            ]
        )
        orchestrator = RecursiveRepairPlanningOrchestrator(
            client,
            PlanningBudget(max_model_calls=20, max_refinements_per_node=3, max_equivalent_decompositions=10),
        )
        with self.assertRaisesRegex(PlanningBlocked, "maximum dependency resolution attempts"):
            await orchestrator.run("Build a veterinary appointment management system")


if __name__ == "__main__":
    unittest.main()
