import json
import unittest

from packages.software_projects.planning.global_repair import (
    GlobalRepairOutput,
    GlobalRepairPlanningOrchestrator,
    global_finding_records,
    validate_global_repair_output,
)
from packages.software_projects.planning.live_planning import (
    FailureClass,
    GlobalValidatorOutput,
    PlanningBlocked,
    PlanningBudget,
    ProposedNode,
    RequirementsAnalysis,
    StructuredModelResult,
)


def node(node_id, responsibility, **values):
    data={
        "node_id":node_id,
        "title":node_id.replace("_"," "),
        "node_type":"domain_engine",
        "objective":responsibility,
        "responsibilities":[responsibility],
        "linked_requirement_ids":["R-001"],
        "inputs":["request"],
        "outputs":["result"],
        "dependencies":[],
        "state_effects":["record state change"],
        "invariants":["state remains internally consistent"],
        "failure_cases":["reject invalid state transition"],
        "security_constraints":["authorize the actor"],
        "persistence_behavior":["persist durable state"],
        "required_artifacts":[f"{node_id} implementation"],
        "required_tests":[f"{node_id} happy path and invalid transition test"],
        "assumptions":[],
        "scope_claims":[],
        "children":[],
    }
    data.update(values)
    return data


class ScriptedModel:
    provider="scripted"
    model_id="fake-v1"

    def __init__(self,responses):
        self.responses=list(responses)
        self.calls=[]

    async def generate_structured(self,*,role,context,output_schema,request_id,timeout_seconds,attempt=1):
        self.calls.append((role,context))
        value=self.responses.pop(0)
        if isinstance(value,Exception):
            return StructuredModelResult(
                provider=self.provider,model_id=self.model_id,request_id=request_id,attempt=attempt,
                latency_ms=1,validation_errors=[str(value)],failure_classification=FailureClass.MALFORMED_OUTPUT,
                context_digest=context.digest(),
            )
        output=output_schema.model_validate(value).model_dump(mode="json")
        return StructuredModelResult(
            provider=self.provider,model_id=self.model_id,request_id=request_id,attempt=attempt,
            input_tokens=10,output_tokens=10,latency_ms=1,structured_output=output,
            raw_response=json.dumps(value),context_digest=context.digest(),
        )


ANALYST={
    "root_objective":"Manage veterinary appointments",
    "requirements":[{
        "requirement_id":"R-001",
        "source_excerpt":"Build a veterinary appointment management system",
        "normalized_requirement":"Manage veterinary appointments",
        "category":"behavior",
        "priority":"mandatory",
        "acceptance_criteria":["appointments can be managed end to end"],
    }],
    "global_exclusions":[],
}

APPROVE={
    "disposition":"approve",
    "ready_for_implementation":True,
    "semantic_coverage":"complete",
    "reasoning_summary":"ready",
}

GLOBAL_OK={
    "approved":True,
    "semantic_completeness":"complete",
    "reasoning_summary":"complete",
}


class GlobalRepairUnitTests(unittest.TestCase):
    def test_repair_output_must_cover_every_global_finding(self):
        analysis=RequirementsAnalysis.model_validate(ANALYST)
        current=[ProposedNode.model_validate(node("appointments","manage appointment state"))]
        failed=GlobalValidatorOutput.model_validate({
            "approved":False,
            "semantic_completeness":"partial",
            "missing_subsystems":["staff availability is absent"],
            "incomplete_user_journeys":["rescheduling has no end-to-end path"],
            "reasoning_summary":"two gaps",
        })
        findings=global_finding_records(failed)
        output=GlobalRepairOutput.model_validate({
            "directives":[{
                "directive_id":"GR-001",
                "action":"revalidate",
                "finding_ids":[findings[0]["finding_id"]],
                "target_node_ids":["appointments"],
                "rationale":"existing appointment node must account for staff availability",
            }]
        })
        errors=validate_global_repair_output(output,findings,current,analysis,PlanningBudget())
        self.assertTrue(any("omitted finding IDs" in item for item in errors))


class GlobalRepairOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_subsystem_is_added_and_global_validation_reruns(self):
        appointments=node("appointments","manage appointment state")
        availability=node("availability","manage veterinary staff availability")
        failed={
            "approved":False,
            "semantic_completeness":"missing subsystem",
            "missing_subsystems":["Veterinary staff availability is absent"],
            "reasoning_summary":"appointment assignment cannot be completed without availability",
        }
        finding_id=global_finding_records(GlobalValidatorOutput.model_validate(failed))[0]["finding_id"]
        repair={
            "directives":[{
                "directive_id":"GR-001",
                "action":"add",
                "finding_ids":[finding_id],
                "proposed_nodes":[availability],
                "rationale":"add the missing availability responsibility without rewriting the appointment leaf",
            }]
        }
        client=ScriptedModel([
            ANALYST,
            {"nodes":[appointments]},
            APPROVE,
            failed,
            repair,
            APPROVE,
            APPROVE,
            GLOBAL_OK,
        ])
        orchestrator=GlobalRepairPlanningOrchestrator(client,PlanningBudget(max_model_calls=20))
        result=await orchestrator.run("Build a veterinary appointment management system")
        self.assertTrue(result["global"].approved)
        self.assertEqual(result["global_repair_rounds"],1)
        self.assertEqual({x.node_id for x in result["nodes"]},{"appointments","availability"})
        self.assertEqual(
            [role for role,_ in client.calls],
            ["requirements_analyst","planner","validator","global_validator","global_repair_planner","validator","validator","global_validator"],
        )
        self.assertEqual(orchestrator.global_repair_traces[0]["round"],1)

    async def test_superficial_test_finding_revalidates_and_patches_existing_leaf(self):
        appointments=node("appointments","manage appointment state",required_tests=["appointment can be created"])
        failed={
            "approved":False,
            "semantic_completeness":"partial",
            "superficial_tests":["Appointment cancellation failure behavior is not tested"],
            "reasoning_summary":"test coverage is superficial",
        }
        finding_id=global_finding_records(GlobalValidatorOutput.model_validate(failed))[0]["finding_id"]
        repair={
            "directives":[{
                "directive_id":"GR-001",
                "action":"revalidate",
                "finding_ids":[finding_id],
                "target_node_ids":["appointments"],
                "rationale":"the existing appointment leaf owns cancellation behavior",
            }]
        }
        needs_test={
            "disposition":"patch_contract",
            "ready_for_implementation":False,
            "semantic_coverage":"partial",
            "missing_tests":["cancellation from an invalid state is rejected"],
            "fields_to_patch":["required_tests"],
            "reasoning_summary":"global finding requires a concrete negative-path test",
        }
        patch={
            "node_id":"appointments",
            "resolved_finding_ids":[],
            "patch":{"required_tests":["cancellation from an invalid state is rejected"]},
        }
        client=ScriptedModel([
            ANALYST,
            {"nodes":[appointments]},
            APPROVE,
            failed,
            repair,
            needs_test,
            patch,
            APPROVE,
            GLOBAL_OK,
        ])
        orchestrator=GlobalRepairPlanningOrchestrator(client,PlanningBudget(max_model_calls=20))
        result=await orchestrator.run("Build a veterinary appointment management system")
        self.assertTrue(result["global"].approved)
        self.assertIn(
            "cancellation from an invalid state is rejected",
            result["nodes"][0].required_tests,
        )
        self.assertEqual(
            [role for role,_ in client.calls],
            ["requirements_analyst","planner","validator","global_validator","global_repair_planner","validator","contract_patcher","validator","global_validator"],
        )

    async def test_global_repair_exhaustion_blocks_instead_of_returning_failed_plan(self):
        appointments=node("appointments","manage appointment state")
        failed={
            "approved":False,
            "semantic_completeness":"partial",
            "superficial_tests":["negative path remains superficial"],
            "reasoning_summary":"still incomplete",
        }
        finding_id=global_finding_records(GlobalValidatorOutput.model_validate(failed))[0]["finding_id"]
        repair={
            "directives":[{
                "directive_id":"GR-001",
                "action":"revalidate",
                "finding_ids":[finding_id],
                "target_node_ids":["appointments"],
                "rationale":"revalidate the owning leaf",
            }]
        }
        client=ScriptedModel([
            ANALYST,{"nodes":[appointments]},APPROVE,failed,repair,APPROVE,failed,
        ])
        orchestrator=GlobalRepairPlanningOrchestrator(
            client,PlanningBudget(max_model_calls=20),max_global_repair_rounds=1
        )
        with self.assertRaisesRegex(PlanningBlocked,"global validation remained unresolved"):
            await orchestrator.run("Build a veterinary appointment management system")


if __name__=="__main__":
    unittest.main()
