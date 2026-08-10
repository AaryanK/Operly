import json
from sqlalchemy import desc, select

from packages.custom_software.planner import build_software_plan, revise_plan
from packages.custom_software.schema import SoftwarePlan
from packages.database.custom_software_models import SoftwarePlanRecord, SoftwarePlanVersion, PlanningModelInvocation
from packages.custom_software.live_planning import (
    OllamaPlanningClient,
    PlanningMode,
    PlannerUnavailable,
    PlanningBlocked,
    planning_mode,
)
from packages.custom_software.live_projection import project_live_envelope
from packages.custom_software.planning_orchestrator import RecursiveRepairPlanningOrchestrator
from packages.custom_software.planning_output_normalizer import NormalizingPlanningClient


class PlanConflict(ValueError):
    pass


async def create_plan(db, tenant_id, user_id, prompt):
    mode = planning_mode()
    if mode == PlanningMode.UNAVAILABLE:
        raise PlannerUnavailable("planner_unavailable")
    row = SoftwarePlanRecord(
        tenant_id=tenant_id,
        prompt=prompt,
        created_by=user_id,
        status="planning",
    )
    db.add(row)
    await db.flush()
    if mode == PlanningMode.LIVE_LLM:
        async def persist_result(role, node_id, result):
            db.add(_invocation(row, tenant_id, role, node_id, result))
            await db.commit()

        orchestrator = RecursiveRepairPlanningOrchestrator(
            NormalizingPlanningClient(OllamaPlanningClient()), on_result=persist_result
        )
        try:
            outcome = await orchestrator.run(prompt)
            outcome["invocations"] = orchestrator.results
        except Exception:
            row.status = "planning_blocked"
            await db.commit()
            raise
        planned = _live_plan(prompt, outcome)
    else:
        planned = build_software_plan(prompt)
        data = planned.model_dump()
        data["planningMode"] = "deterministic_test"
        data["planningMetrics"]["planningMode"] = "deterministic_test"
        for item in data["requirementLedger"]:
            item["planningMode"] = "deterministic_test"
        for item in data["planTree"]:
            item["planningMode"] = "deterministic_test"
        planned = SoftwarePlan.model_validate(data)
    row.status = "draft"
    version = SoftwarePlanVersion(
        tenant_id=tenant_id,
        plan_id=row.id,
        version=1,
        plan_json=planned.model_dump_json(),
        requirement_ledger_json=json.dumps([x.model_dump() for x in planned.requirementLedger]),
        plan_tree_json=json.dumps([x.model_dump() for x in planned.planTree]),
        validation_json=json.dumps(planned.globalValidation),
        semantic_diff_json=planned.semanticDiff.model_dump_json() if planned.semanticDiff else "{}",
        created_by=user_id,
    )
    db.add(version)
    await db.commit()
    await db.refresh(row)
    return row, version, planned


def _invocation(row, tenant_id, role, node_id, result):
    return PlanningModelInvocation(
        tenant_id=tenant_id,
        plan_id=row.id,
        plan_version=1,
        node_id=node_id,
        role=role,
        planning_mode="live_llm",
        provider=result.provider,
        model_id=result.model_id,
        request_id=result.request_id,
        context_digest=result.context_digest,
        attempt=result.attempt,
        structured_output_json=json.dumps(result.structured_output or {}),
        raw_response=result.raw_response,
        validation_errors_json=json.dumps(result.validation_errors),
        retry_history_json=json.dumps(result.retry_history),
        latency_ms=result.latency_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        failure_classification=result.failure_classification,
    )


def _live_plan(prompt, outcome):
    analysis = outcome["analysis"]
    nodes = outcome["nodes"]
    validations = outcome["validations"]
    budget = outcome["budget"]

    ledger = []
    for req in analysis.requirements:
        linked = [x.node_id for x in nodes if req.requirement_id in x.linked_requirement_ids]
        ledger.append(
            {
                "id": req.requirement_id,
                "originalSource": "original_request",
                "exactText": req.source_excerpt,
                "normalizedMeaning": req.normalized_requirement,
                "mandatory": req.priority.lower() != "optional",
                "category": req.category,
                "acceptanceCriteria": req.acceptance_criteria,
                "relatedPlanNodeIds": linked,
                "relatedArtifactIds": [
                    artifact
                    for node in nodes
                    if req.requirement_id in node.linked_requirement_ids
                    for artifact in node.required_artifacts
                ],
                "relatedTestIds": [
                    test
                    for node in nodes
                    if req.requirement_id in node.linked_requirement_ids
                    for test in node.required_tests
                ],
                "coverageStatus": "implementation_ready" if linked else "unplanned",
                "verificationStatus": "unverified",
                "planningMode": "live_llm",
                "explicitTerms": req.explicit_terms,
                "exclusions": req.exclusions,
                "ambiguities": req.ambiguities,
                "conflicts": req.conflicts,
                "assumptions": req.assumptions,
            }
        )

    tree = []
    for node in nodes:
        verdict = validations[node.node_id]
        tree.append(
            {
                "id": node.node_id,
                "parentId": None,
                "originalRequirementIds": node.linked_requirement_ids,
                "title": node.title,
                "objective": node.objective,
                "description": node.objective,
                "nodeType": node.node_type,
                "inputs": node.inputs,
                "outputs": node.outputs,
                "dependencies": node.dependencies,
                "constraints": node.assumptions,
                "securityRequirements": node.security_constraints,
                "failureCases": node.failure_cases,
                "acceptanceCriteria": [
                    criterion
                    for req in analysis.requirements
                    if req.requirement_id in node.linked_requirement_ids
                    for criterion in req.acceptance_criteria
                ],
                "requiredArtifacts": node.required_artifacts,
                "requiredTests": node.required_tests,
                "status": "implementation_ready",
                "validation": {
                    "readyForImplementation": True,
                    "missingInformation": verdict.missing_information,
                    "ambiguousBehavior": verdict.ambiguous_behavior,
                    "missingInputs": verdict.missing_inputs,
                    "missingOutputs": verdict.missing_outputs,
                    "missingInvariants": verdict.missing_invariants,
                    "missingDependencies": verdict.missing_dependencies,
                    "missingFailureHandling": verdict.missing_failure_handling,
                    "missingSecurityRules": verdict.missing_security_rules,
                    "missingPersistenceBehavior": verdict.missing_persistence_behavior,
                    "missingTests": verdict.missing_tests,
                    "conflicts": verdict.requirement_conflicts,
                    "recommendedDecompositionAreas": verdict.recommended_decomposition,
                },
                "implementationEvidence": [],
                "childIds": [],
                "version": 1,
                "provenance": {"planningMode": "live_llm"},
                "planningMode": "live_llm",
                "responsibilities": node.responsibilities,
                "stateEffects": node.state_effects,
                "invariants": node.invariants,
                "persistenceBehavior": node.persistence_behavior,
            }
        )

    mandatory = [x for x in ledger if x["mandatory"]]
    mapped = sum(bool(x["relatedPlanNodeIds"]) for x in mandatory)
    global_ok = outcome["global"].approved and mapped == len(mandatory)
    invocations = outcome.get("invocations", [])
    input_tokens = sum(result.input_tokens for _, _, result in invocations)
    output_tokens = sum(result.output_tokens for _, _, result in invocations)

    base = project_live_envelope(
        build_software_plan(prompt).model_dump(), analysis, nodes, ledger
    )
    base["provenance"] = {
        **base.get("provenance", {}),
        "planningMode": "live_llm",
        "providerModel": (
            f"{invocations[0][2].provider}/{invocations[0][2].model_id}"
            if invocations
            else "unknown"
        ),
        "invocationCount": len(invocations),
        "contextPacketsStoredAsDigests": True,
        "globalRepairRounds": outcome.get("global_repair_rounds", 0),
        "dependencyResolutionCount": len(outcome.get("dependency_resolution_traces", [])),
        "semanticAuthority": "validated_recursive_plan",
        "legacySemanticDefaultsDiscarded": True,
    }
    base.update(
        {
            "summary": analysis.root_objective,
            "primaryGoal": analysis.root_objective,
            "requirementLedger": ledger,
            "planTree": tree,
            "planningMode": "live_llm",
            "planningBudget": {
                "maxDepth": budget.max_depth,
                "maxNodes": budget.max_nodes,
                "maxRefinementsPerNode": budget.max_refinements_per_node,
                "maxModelCalls": budget.max_model_calls,
                "maxTokens": budget.max_tokens,
                "maxElapsedSeconds": budget.max_elapsed_seconds,
            },
            "planningMetrics": {
                "mandatoryRequirementsMapped": mapped,
                "mandatoryRequirementsTotal": len(mandatory),
                "planNodesReady": len(tree),
                "planNodesTotal": len(tree),
                "executableTestsMapped": sum(bool(x["relatedTestIds"]) for x in ledger),
                "unresolvedValidatorFindings": 0,
                "dependencyComplete": True,
                "globalValidationPassed": global_ok,
                "approvalBlockedReasons": [] if global_ok else ["live global validation failed"],
                "planningMode": "live_llm",
                "planningCallsUsed": budget.calls,
                "inputTokensUsed": input_tokens,
                "outputTokensUsed": output_tokens,
                "blockedNodes": 0,
                "nodesRequiringDecomposition": 0,
                "testSpecificationCoverage": sum(bool(x["relatedTestIds"]) for x in ledger),
            },
            "globalValidation": outcome["global"].model_dump(mode="json")
            | {"passed": global_ok, "planningMode": "live_llm"},
        }
    )
    return SoftwarePlan.model_validate(base)


async def owned_plan(db, tenant_id, plan_id):
    row = await db.get(SoftwarePlanRecord, plan_id)
    if not row or row.tenant_id != tenant_id:
        raise LookupError("Software plan not found")
    return row


async def plan_version(db, row, version=None):
    number = version or row.current_version
    result = await db.scalar(
        select(SoftwarePlanVersion).where(
            SoftwarePlanVersion.plan_id == row.id,
            SoftwarePlanVersion.tenant_id == row.tenant_id,
            SoftwarePlanVersion.version == number,
        )
    )
    if not result:
        raise LookupError("Software plan version not found")
    return result, SoftwarePlan.model_validate_json(result.plan_json)


async def revise(db, row, user_id, request, expected):
    if row.current_version != expected:
        raise PlanConflict("Software plan changed; refresh before revising")
    _, current = await plan_version(db, row)
    updated = revise_plan(current, request)
    if not updated.semanticDiff or not updated.semanticDiff.structuralChange:
        raise PlanConflict("Revision produced no structural plan change")
    row.current_version += 1
    row.status = "draft"
    row.approved_version = None
    version = SoftwarePlanVersion(
        tenant_id=row.tenant_id,
        plan_id=row.id,
        version=row.current_version,
        plan_json=updated.model_dump_json(),
        requirement_ledger_json=json.dumps([x.model_dump() for x in updated.requirementLedger]),
        plan_tree_json=json.dumps([x.model_dump() for x in updated.planTree]),
        validation_json=json.dumps(updated.globalValidation),
        semantic_diff_json=updated.semanticDiff.model_dump_json() if updated.semanticDiff else "{}",
        revision_request=request,
        created_by=user_id,
    )
    db.add(version)
    await db.commit()
    await db.refresh(row)
    return version, updated


async def approve(db, row, expected):
    if row.current_version != expected:
        raise PlanConflict("Software plan changed; refresh before approving")
    _, plan = await plan_version(db, row, expected)
    if not plan.planningMetrics or not plan.planningMetrics.globalValidationPassed:
        raise PlanConflict("Approval is blocked until global validation passes")
    if row.approved_version == expected:
        return row
    row.approved_version = expected
    row.status = "approved"
    await db.commit()
    await db.refresh(row)
    return row


def plan_json(row, version, plan):
    return {
        "id": row.id,
        "status": row.status,
        "currentVersion": row.current_version,
        "approvedVersion": row.approved_version,
        "version": version.version,
        "prompt": row.prompt,
        "plan": plan.model_dump(),
    }
