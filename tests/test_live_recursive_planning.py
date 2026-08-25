import asyncio
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from packages.software_projects.planning.live_planning import (
    ContractPatchOutput, FailureClass, LivePlanningOrchestrator, PlanningBlocked, PlanningBudget, PlanningContextPacket,
    PlanningMode, PlannerOutput, ProposedNode, StructuredModelResult,
    RequirementPartitionOutput, ValidatorOutput, accepted_partial_contract, classify_failure, deterministic_readiness,
    apply_contract_patch, canonicalize_minimal_contract, merge_preserved_contract, normalized_plan_digest, planning_mode, structural_errors,
    normalize_platform_default_dependencies, scope_errors, validate_partition_output,
)


def node(node_id, responsibility, **values):
    data={"node_id":node_id,"title":node_id.replace("_"," "),"node_type":"domain_engine","objective":responsibility,
        "responsibilities":[responsibility],"linked_requirement_ids":["R-001"],"inputs":["lexemes"],"outputs":["transformations"],
        "dependencies":[],"state_effects":["append trace"],"invariants":["stable ordering"],"failure_cases":["reject malformed rules"],
        "security_constraints":["validate input"],"persistence_behavior":["retain provenance"],"required_artifacts":["engine module"],
        "required_tests":["same input and seed produces identical result"],"assumptions":[],"children":[]}
    data.update(values); return data


class ScriptedModel:
    provider="scripted"; model_id="fake-v1"
    def __init__(self,responses): self.responses=list(responses); self.calls=[]
    async def generate_structured(self,*,role,context,output_schema,request_id,timeout_seconds,attempt=1):
        self.calls.append((role,context)); value=self.responses.pop(0)
        if isinstance(value,Exception):
            return StructuredModelResult(provider=self.provider,model_id=self.model_id,request_id=request_id,attempt=attempt,latency_ms=1,validation_errors=[str(value)],failure_classification=FailureClass.MALFORMED_OUTPUT,context_digest=context.digest())
        output=output_schema.model_validate(value).model_dump(mode="json")
        return StructuredModelResult(provider=self.provider,model_id=self.model_id,request_id=request_id,attempt=attempt,input_tokens=10,output_tokens=10,latency_ms=1,structured_output=output,raw_response=json.dumps(value),context_digest=context.digest())


class LivePlanningUnitTests(unittest.TestCase):
    def test_mode_is_explicit_and_live_without_credentials_is_unavailable(self):
        with patch.dict(os.environ,{"OPERLY_PLANNING_MODE":"live_llm","OLLAMA_API_KEY":""},clear=False): self.assertEqual(planning_mode(),PlanningMode.UNAVAILABLE)
        with patch.dict(os.environ,{"OPERLY_PLANNING_MODE":"deterministic_test"},clear=False): self.assertEqual(planning_mode(),PlanningMode.DETERMINISTIC_TEST)

    def test_context_digest_is_stable_and_sensitive(self):
        one=PlanningContextPacket(role="planner",untrusted_requirements={"x":"one"}); two=PlanningContextPacket(role="planner",untrusted_requirements={"x":"two"})
        self.assertEqual(one.digest(),one.digest()); self.assertNotEqual(one.digest(),two.digest())

    def test_structural_validation_rejects_unknown_requirements_exclusions_and_empty_scope(self):
        proposed=ProposedNode.model_validate(node("bad","technician dispatch",linked_requirement_ids=["R-999"],responsibilities=[]))
        errors=structural_errors([proposed],{"R-001"},["technician"],PlanningBudget())
        self.assertTrue(any("empty responsibilities" in x for x in errors)); self.assertTrue(any("invalid requirement" in x for x in errors)); self.assertTrue(any("excluded" in x for x in errors))

    def test_readiness_requires_deterministic_and_semantic_approval(self):
        proposed=ProposedNode.model_validate(node("engine","apply rules")); verdict=ValidatorOutput(ready_for_implementation=False,semantic_coverage="partial",missing_tests=["malformed rule test"],reasoning_summary="too broad")
        ready,findings=deterministic_readiness(proposed,verdict); self.assertFalse(ready); self.assertIn("malformed rule test",findings)

    def test_normalized_digest_detects_cosmetic_equivalence(self):
        a=ProposedNode.model_validate(node("a","Apply ordered rules.",title="Ordered executor")); b=ProposedNode.model_validate(node("b","apply ordered rules",title="ordered executor"))
        self.assertEqual(normalized_plan_digest([a]),normalized_plan_digest([b]))

    def test_partition_must_cover_requirements_findings_and_only_accepted_fields(self):
        output=RequirementPartitionOutput.model_validate({"partitions":[{"partition_id":"p1","title":"Parser","objective":"Parse rules","responsibility":"parse one rule","linked_requirement_ids":["R-001"],"addressed_finding_ids":[],"preserved_contract":{"outputs":["unaccepted output"]}}]})
        errors=validate_partition_output(output,{"R-001","R-002"},{"F-001"},{"inputs":["rules"]})
        self.assertTrue(any("unaccepted" in x for x in errors));self.assertTrue(any("omitted linked requirements" in x for x in errors));self.assertTrue(any("omitted readiness finding IDs" in x for x in errors))

    def test_scope_authority_rejects_unjustified_formats_and_accepts_essential_typed_boundary(self):
        linked=[{"requirement_id":"R-001","source_excerpt":"accept lexemes"}]
        invented=ProposedNode.model_validate(node("ingest","ingest lexemes",inputs=["CSV file upload"]))
        self.assertTrue(any("csv" in x for x in scope_errors(invented,linked)))
        minimal=ProposedNode.model_validate(node("ingest","accept a typed Lexeme object",inputs=["typed Lexeme object"],scope_claims=[{"subject":"typed Lexeme object","authority":"derived_essential_requirement","linked_requirement_ids":["R-001"],"justification":"The engine needs a stable in-process lexeme representation","blocks_readiness":True}]))
        self.assertEqual(scope_errors(minimal,linked),[])

    def test_platform_defaults_become_assumptions_not_graph_dependencies(self):
        proposed=ProposedNode.model_validate(node("input","accept typed input",dependencies=["platform_defaults.input_boundary","rule_parser"]))
        normalized=normalize_platform_default_dependencies(proposed)
        self.assertEqual(normalized.dependencies,["rule_parser"]);self.assertIn("Use declared platform_defaults.input_boundary",normalized.assumptions)

    def test_contract_patch_cannot_modify_locked_field(self):
        proposed=ProposedNode.model_validate(node("tracker","track state"))
        patch=ContractPatchOutput(node_id="tracker",resolved_finding_ids=[],patch={"outputs":["replacement output"]})
        with self.assertRaises(PlanningBlocked):apply_contract_patch(proposed,patch,{"inputs"})

    def test_minimal_contract_replaces_pruned_document_with_typed_artifact(self):
        proposed=ProposedNode.model_validate(node("input_handler","accept inputs",required_artifacts=["Input Specification Document"],invariants=[]))
        minimal=canonicalize_minimal_contract(proposed,["R-001"],["Input Specification Document"])
        self.assertEqual(minimal.required_artifacts,["typed input handler contract"]);self.assertTrue(minimal.invariants)
        self.assertEqual(minimal.scope_claims[-1].authority,"derived_essential_requirement")


class FakeModelOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_incomplete_node_is_patched_revalidated_and_ready_without_children(self):
        analyst={"root_objective":"Track transformations","requirements":[{"requirement_id":"R-001","source_excerpt":"track transformations","normalized_requirement":"Track transformations","category":"behavior","priority":"mandatory","acceptance_criteria":["trace retains order"]}],"global_exclusions":[]}
        atomic=node("tracker","record one transformation trace entry",inputs=[])
        patch_verdict={"disposition":"approve","ready_for_implementation":True,"semantic_coverage":"complete","fields_to_patch":[],"fields_to_preserve":["outputs","invariants","required_tests"],"reasoning_summary":"semantic validator overlooked deterministic missing input"}
        patch={"node_id":"tracker","resolved_finding_ids":["missing_inputs"],"patch":{"inputs":["previous lexeme form","resulting lexeme form","execution sequence number"]}}
        approve={"disposition":"approve","ready_for_implementation":True,"semantic_coverage":"complete","reasoning_summary":"ready"}
        global_ok={"approved":True,"semantic_completeness":"complete","reasoning_summary":"complete"}
        client=ScriptedModel([analyst,{"nodes":[atomic]},patch_verdict,patch,approve,global_ok])
        orchestrator=LivePlanningOrchestrator(client,PlanningBudget(max_model_calls=10));result=await orchestrator.run("Track transformations")
        self.assertEqual([x[0] for x in client.calls],["requirements_analyst","planner","validator","contract_patcher","validator","global_validator"])
        self.assertEqual(result["nodes"][0].child_ids if hasattr(result["nodes"][0],"child_ids") else result["nodes"][0].children,[])
        self.assertIn("missing_inputs",orchestrator.correction_traces[-1]["resolved_finding_ids"])

    async def test_patch_claim_without_resolved_findings_is_blocked_as_ineffective(self):
        analyst={"root_objective":"Track transformations","requirements":[{"requirement_id":"R-001","source_excerpt":"track transformations","normalized_requirement":"Track transformations","category":"behavior","priority":"mandatory","acceptance_criteria":["trace retains order"]}],"global_exclusions":[]}
        atomic=node("tracker","record one transformation trace entry",invariants=[])
        verdict={"disposition":"patch_contract","ready_for_implementation":False,"semantic_coverage":"partial","missing_invariants":["trace ordering remains undefined"],"fields_to_patch":["invariants"],"reasoning_summary":"invariant missing"}
        patch={"node_id":"tracker","resolved_finding_ids":["missing_invariants:claimed"],"patch":{"invariants":["trace order equals execution order"]}}
        client=ScriptedModel([analyst,{"nodes":[atomic]},verdict,patch,verdict,patch,verdict,patch,verdict])
        orchestrator=LivePlanningOrchestrator(client,PlanningBudget(max_model_calls=10,max_equivalent_decompositions=1))
        with self.assertRaisesRegex(PlanningBlocked,"no findings resolved"):await orchestrator.run("Track transformations")
        self.assertNotIn("requirement_partitioner",[x[0] for x in client.calls])

    async def test_maximum_patch_attempts_block_atomic_node(self):
        analyst={"root_objective":"Track transformations","requirements":[{"requirement_id":"R-001","source_excerpt":"track transformations","normalized_requirement":"Track transformations","category":"behavior","priority":"mandatory","acceptance_criteria":["trace retains order"]}],"global_exclusions":[]}
        atomic=node("tracker","record one transformation trace entry",invariants=[])
        verdict={"disposition":"patch_contract","ready_for_implementation":False,"semantic_coverage":"partial","missing_invariants":["trace ordering remains undefined"],"fields_to_patch":["invariants"],"reasoning_summary":"invariant missing"}
        patch={"node_id":"tracker","resolved_finding_ids":[],"patch":{"invariants":["trace order equals execution order"]}}
        orchestrator=LivePlanningOrchestrator(ScriptedModel([analyst,{"nodes":[atomic]},verdict,patch,verdict]),PlanningBudget(max_model_calls=10,max_refinements_per_node=1))
        with self.assertRaisesRegex(PlanningBlocked,"maximum contract patch attempts"):await orchestrator.run("Track transformations")

    async def test_missing_dependency_creates_work_item_instead_of_rewriting_node(self):
        analyst={"root_objective":"Track transformations","requirements":[{"requirement_id":"R-001","source_excerpt":"track transformations","normalized_requirement":"Track transformations","category":"behavior","priority":"mandatory","acceptance_criteria":["trace retains order"]}],"global_exclusions":[]}
        atomic=node("tracker","record one transformation trace entry")
        verdict={"disposition":"resolve_dependency","ready_for_implementation":False,"semantic_coverage":"partial","missing_dependencies":["TransformationTrace schema"],"reasoning_summary":"dependency undefined"}
        orchestrator=LivePlanningOrchestrator(ScriptedModel([analyst,{"nodes":[atomic]},verdict]),PlanningBudget(max_model_calls=10))
        with self.assertRaises(PlanningBlocked):await orchestrator.run("Track transformations")
        self.assertEqual(orchestrator.dependency_work_items[0]["blocked_node_id"],"tracker")
        self.assertEqual(orchestrator.dependency_work_items[0]["state"],"queued")

    async def test_validator_prunes_invented_format_to_minimal_typed_boundary_without_depth_growth(self):
        analyst={"root_objective":"Accept lexemes","requirements":[{"requirement_id":"R-001","source_excerpt":"accept lexemes","normalized_requirement":"Accept lexemes","category":"input","priority":"mandatory","acceptance_criteria":["typed lexeme is accepted"]}],"global_exclusions":[]}
        invented=node("lexeme_input","accept lexemes",inputs=["CSV file upload"])
        prune={"disposition":"prune","ready_for_implementation":False,"semantic_coverage":"partial","irrelevant_scope_expansion":["CSV file upload"],"minimal_contract_guidance":["Use a typed Lexeme object"],"reasoning_summary":"format is unrequired"}
        minimal=node("lexeme_input","accept lexemes",inputs=["typed Lexeme object"],scope_claims=[{"subject":"typed Lexeme object","authority":"derived_essential_requirement","linked_requirement_ids":["R-001"],"justification":"Stable in-process input boundary","blocks_readiness":True}])
        approve={"disposition":"approve","ready_for_implementation":True,"semantic_coverage":"complete","reasoning_summary":"minimal and ready"}
        global_ok={"approved":True,"semantic_completeness":"complete","reasoning_summary":"complete"}
        client=ScriptedModel([analyst,{"nodes":[invented]},prune,{"node":minimal},approve,global_ok])
        result=await LivePlanningOrchestrator(client,PlanningBudget(max_model_calls=10)).run("Accept lexemes")
        self.assertEqual([x[0] for x in client.calls],["requirements_analyst","planner","validator","contract_expander","validator","global_validator"])
        self.assertEqual(result["nodes"][0].inputs,["typed Lexeme object"])

    async def test_separate_roles_reject_and_refine_only_the_target_branch(self):
        analyst={"root_objective":"Deterministic ordered sound-change engine","requirements":[{"requirement_id":"R-001","source_excerpt":"apply eligible rules in order","normalized_requirement":"Apply eligible sound-change rules in order and retain provenance","category":"domain_behavior","priority":"mandatory","acceptance_criteria":["identical inputs and seeds yield identical transformations"],"explicit_terms":["sound-change rules","lexemes"],"exclusions":[],"ambiguities":[],"conflicts":[],"assumptions":[]}],"global_exclusions":["technician"],"questions_requiring_user_input":[],"safe_assumptions":[]}
        broad=node("evolution_engine","apply rules and store traces",responsibilities=["parse rules","execute rules","record traces"])
        reject={"disposition":"decompose","ready_for_implementation":False,"semantic_coverage":"partial","missing_information":[],"ambiguous_behavior":["rule ordering contract is vague"],"missing_inputs":[],"missing_outputs":[],"missing_invariants":["seed determinism"],"missing_dependencies":[],"missing_failure_handling":[],"missing_security_rules":[],"missing_persistence_behavior":[],"missing_tests":["intermediate trace assertion"],"requirement_conflicts":[],"irrelevant_concepts":[],"recommended_decomposition":["ordered executor"],"reasoning_summary":"broad node rejected"}
        responsibility="apply one eligible ordered rule and emit its trace"
        leaf=node("ordered_executor",responsibility,inputs=["rule stream"])
        partition={"partitions":[{"partition_id":"ordered_executor","title":"Ordered executor","objective":"Apply one eligible rule deterministically","responsibility":responsibility,"linked_requirement_ids":["R-001"],"addressed_finding_ids":["F-001","F-002","F-003","F-004","F-005"],"preserved_contract":{"inputs":["lexemes"],"outputs":["transformations"],"state_effects":["append trace"],"failure_cases":["reject malformed rules"],"security_constraints":["validate input"],"persistence_behavior":["retain provenance"],"required_artifacts":["engine module"]}}]}
        expansion={"node":leaf,"applied_preserved_fields":["inputs","outputs","state_effects","failure_cases","security_constraints","persistence_behavior","required_artifacts"]}
        approve={"disposition":"approve","ready_for_implementation":True,"semantic_coverage":"complete","missing_information":[],"ambiguous_behavior":[],"missing_inputs":[],"missing_outputs":[],"missing_invariants":[],"missing_dependencies":[],"missing_failure_handling":[],"missing_security_rules":[],"missing_persistence_behavior":[],"missing_tests":[],"requirement_conflicts":[],"irrelevant_concepts":[],"recommended_decomposition":[],"reasoning_summary":"bounded and testable"}
        global_ok={"approved":True,"semantic_completeness":"complete","missing_subsystems":[],"incompatible_interfaces":[],"missing_integrations":[],"missing_state_transitions":[],"uncovered_requirements":[],"superficial_tests":[],"irrelevant_concepts":[],"contradictions":[],"incomplete_user_journeys":[],"reasoning_summary":"complete"}
        client=ScriptedModel([analyst,{"nodes":[broad]},reject,partition,expansion,approve,global_ok])
        result=await LivePlanningOrchestrator(client,PlanningBudget(max_model_calls=10)).run("Design an ordered linguistic sound-change engine")
        self.assertEqual([x[0] for x in client.calls],["requirements_analyst","planner","validator","requirement_partitioner","contract_expander","validator","global_validator"])
        self.assertEqual(result["nodes"][0].node_id,"ordered_executor"); self.assertEqual(len(result["nodes"]),1)
        partition_call=client.calls[3][1]; self.assertEqual(partition_call.current_contract["node_id"],"evolution_engine"); self.assertTrue(partition_call.previous_findings)
        expansion_call=client.calls[4][1]; self.assertEqual(expansion_call.current_contract["partition_id"],"ordered_executor")
        self.assertEqual(result["nodes"][0].inputs,["lexemes","rule stream"])
        self.assertEqual(len(result["analysis"].requirements),1); self.assertEqual(len(result["global"].contradictions),0)

    def test_accepted_partial_contract_excludes_deficient_fields_and_merges_exact_values(self):
        original=ProposedNode.model_validate(node("broad","execute rules",inputs=["accepted input"],invariants=["weak invariant"]))
        verdict=ValidatorOutput(ready_for_implementation=False,semantic_coverage="partial",missing_invariants=["seed invariant"],reasoning_summary="incomplete")
        accepted=accepted_partial_contract(original,verdict)
        self.assertEqual(accepted["inputs"],["accepted input"]); self.assertNotIn("invariants",accepted)
        expanded=ProposedNode.model_validate(node("leaf","execute rules",inputs=["new input"]))
        from packages.software_projects.planning.live_planning import PartialContract
        merged=merge_preserved_contract(expanded,PartialContract(inputs=accepted["inputs"]))
        self.assertEqual(merged.inputs,["accepted input","new input"])

    async def test_malformed_output_is_retried_with_provenance(self):
        analyst={"root_objective":"x","requirements":[{"requirement_id":"R-001","source_excerpt":"x","normalized_requirement":"x","category":"behavior","priority":"mandatory","acceptance_criteria":["x"]}],"global_exclusions":[]}
        client=ScriptedModel([ValueError("bad json"),analyst])
        orchestrator=LivePlanningOrchestrator(client,max_attempts=2)
        result=await orchestrator._call("requirements_analyst",PlanningContextPacket(role="requirements_analyst",untrusted_requirements={"x":"x"}),__import__("packages.software_projects.planning.live_planning",fromlist=["RequirementsAnalysis"]).RequirementsAnalysis)
        self.assertEqual(result.root_objective,"x"); self.assertEqual(len(orchestrator.results),2); self.assertEqual(orchestrator.results[1][2].retry_history[0]["attempt"],1)


@unittest.skipUnless(os.getenv("OPERLY_RUN_LIVE_PLANNING_TESTS")=="1" and os.getenv("OLLAMA_API_KEY"),"requires OPERLY_RUN_LIVE_PLANNING_TESTS=1 and OLLAMA_API_KEY")
class LiveModelAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_model_narrow_linguistic_engine(self):
        from packages.software_projects.planning.live_planning import OllamaPlanningClient
        orchestrator=LivePlanningOrchestrator(OllamaPlanningClient(),PlanningBudget(max_model_calls=80,max_elapsed_seconds=900))
        try:
            result=await orchestrator.run("Design an implementation-ready deterministic ordered linguistic sound-change engine. It accepts lexemes, ordered rules, generation, region, community, and a deterministic seed; preserves intermediate transformations and provenance; rejects malformed rules; and is repeatable.")
        except PlanningBlocked:
            Path("C:/tmp/narrow-live-correction-traces.json").write_text(json.dumps({"correction_traces":orchestrator.correction_traces,"dependency_work_items":orchestrator.dependency_work_items},indent=2),encoding="utf-8")
            raise
        requirement_ids={x.requirement_id for x in result["analysis"].requirements}
        partition_outputs=[r.structured_output for role,_,r in orchestrator.results if role=="requirement_partitioner" and r.structured_output]
        partition_coverage={rid for output in partition_outputs for part in output["partitions"] for rid in part["linked_requirement_ids"]}
        leaf_coverage={rid for leaf in result["nodes"] for rid in leaf.linked_requirement_ids}
        self.assertTrue(requirement_ids<=partition_coverage|leaf_coverage)
        self.assertTrue(all(leaf.required_tests and all(len(test.split())>=3 for test in leaf.required_tests) for leaf in result["nodes"]))
        self.assertTrue(result["global"].approved); self.assertGreater(len(result["nodes"]),0)
