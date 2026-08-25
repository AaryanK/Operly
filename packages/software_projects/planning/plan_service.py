import asyncio
import json
import os
import re
from sqlalchemy import desc, select
from pydantic import ValidationError

from packages.software_projects.planning.planner import build_software_plan, revise_plan
from packages.software_projects.planning.schema import SoftwarePlan
from packages.database.custom_software_models import (
    SoftwarePlanRecord,
    SoftwarePlanVersion,
    PlanningModelInvocation,
    PlanningWorkItem,
)
from packages.software_projects.planning.live_planning import (
    PlanningMode,
    PlannerUnavailable,
    PlanningBlocked,
)
from packages.software_projects.planning.model_planning_client import ModelPlanningClient, planning_mode
from packages.software_projects.planning.live_projection import neutral_live_envelope, project_live_envelope
from packages.software_projects.planning.planning_orchestrator import (
    PlanningNeedsUserInput,
    RecursiveRepairPlanningOrchestrator,
)
from packages.software_projects.planning.planning_output_normalizer import NormalizingPlanningClient


class PlanConflict(ValueError):
    pass


_INTERACTION_WORDS = re.compile(
    r"\b(add|approve|book|button|cancel|choose|click|compare|create|delete|dialog|edit|export|form|input|login|navigate|record|remove|save|search|select|submit|update|upload)\b",
    re.IGNORECASE,
)


def _unique_strings(values):
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _is_interactive_requirement(requirement) -> bool:
    text = " ".join(
        [str(requirement.category), requirement.normalized_requirement, requirement.source_excerpt]
    )
    return bool(_INTERACTION_WORDS.search(text))


def _interaction_test_ids(requirement) -> list[str]:
    return [f"interaction_{requirement.requirement_id.lower().replace('-', '_')}"] if _is_interactive_requirement(requirement) else []


def _interaction_acceptance(requirement) -> list[str]:
    criteria = list(requirement.acceptance_criteria)
    if _is_interactive_requirement(requirement):
        criteria.append(
            "Executable interaction test proves the rendered control invokes its domain operation, handles success and rejection, changes state and UI when required, emits no runtime error, and honors reload persistence."
        )
    return _unique_strings(criteria)


def _planning_concurrency() -> int:
    try:
        return max(1, min(int(os.getenv("OPERLY_MAX_CONCURRENT_PLANS", "1")), 8))
    except ValueError:
        return 1


_LIVE_PLANNING_GATE = asyncio.Semaphore(_planning_concurrency())


async def _run_live_plan(db, row, tenant_id, prompt):
    async def persist_result(role, node_id, result):
        db.add(_invocation(row, tenant_id, role, node_id, result))
        await db.commit()

    orchestrator = RecursiveRepairPlanningOrchestrator(
        NormalizingPlanningClient(ModelPlanningClient()), on_result=persist_result
    )
    # Provider/model selection, retries and cross-provider failover happen inside
    # model_runtime. Planning only bounds concurrent structured planning sessions.
    async with _LIVE_PLANNING_GATE:
        outcome = await orchestrator.run(prompt)
    outcome["invocations"] = orchestrator.results
    return _live_plan(prompt, outcome)


async def _clarification_item(db, row):
    return await db.scalar(
        select(PlanningWorkItem)
        .where(
            PlanningWorkItem.plan_id == row.id,
            PlanningWorkItem.tenant_id == row.tenant_id,
            PlanningWorkItem.plan_version == row.current_version,
            PlanningWorkItem.work_type == "user_clarification",
        )
        .order_by(desc(PlanningWorkItem.updated_at))
    )


async def _store_clarification(db, row, questions, history=None, item=None):
    cleaned = [str(question).strip() for question in questions if str(question).strip()][:2]
    if not cleaned:
        raise PlanningBlocked("planner requested clarification without a usable question")
    item = item or await _clarification_item(db, row)
    if item is None:
        item = PlanningWorkItem(
            tenant_id=row.tenant_id,
            plan_id=row.id,
            plan_version=row.current_version,
            node_id="root_clarification",
            work_type="user_clarification",
            priority=1,
        )
        db.add(item)
    item.state = "blocked"
    item.payload_json = json.dumps(
        {
            "questions": cleaned,
            "history": list(history or []),
            "originalPrompt": row.prompt,
        }
    )
    item.findings_json = json.dumps(
        [{"type": "user_clarification", "question": question} for question in cleaned]
    )
    item.blocked_question = cleaned[0]
    row.status = "awaiting_clarification"
    await db.commit()
    return item


def _clarified_prompt(original_prompt, history):
    sections = [original_prompt.strip(), "", "Clarifications supplied by the owner:"]
    for index, turn in enumerate(history, 1):
        questions = [str(value).strip() for value in turn.get("questions", []) if str(value).strip()]
        answer = str(turn.get("answer", "")).strip()
        sections.append(f"Clarification {index}:")
        for question in questions:
            sections.append(f"- OPERLY asked: {question}")
        sections.append(f"- Owner answered: {answer}")
    sections.extend(
        [
            "Treat the owner's clarification as authoritative context for this same planning request.",
            "When the owner delegates a decision to OPERLY, choose sensible conventional defaults from the request and platform boundaries.",
            "Do not ask another question for details that can be safely inferred, such as labels, categories, fields, metrics, severity names, or presentation defaults.",
            "Return a complete implementation-ready plan now. Ask again only for an unresolved security, permission, legal, cost, or data-ownership decision.",
        ]
    )
    return "\n".join(sections)


async def _persist_first_version(db, row, user_id, planned, revision_request=None):
    version = SoftwarePlanVersion(
        tenant_id=row.tenant_id,
        plan_id=row.id,
        version=row.current_version,
        plan_json=planned.model_dump_json(),
        requirement_ledger_json=json.dumps([x.model_dump() for x in planned.requirementLedger]),
        plan_tree_json=json.dumps([x.model_dump() for x in planned.planTree]),
        validation_json=json.dumps(planned.globalValidation),
        semantic_diff_json=planned.semanticDiff.model_dump_json() if planned.semanticDiff else "{}",
        revision_request=revision_request,
        created_by=user_id,
    )
    db.add(version)
    row.status = "draft"
    await db.commit()
    await db.refresh(row)
    return version


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
        try:
            planned = await _run_live_plan(db, row, tenant_id, prompt)
        except PlanningNeedsUserInput as error:
            await _store_clarification(db, row, error.questions)
            error.plan_id = row.id
            raise
        except ValidationError as error:
            row.status = "planning_blocked"
            await db.commit()
            details = "; ".join(
                f"{'.'.join(str(part) for part in item.get('loc', []))}: {item.get('msg', 'invalid')}"
                for item in error.errors()[:8]
            )
            raise PlanningBlocked(f"live plan projection failed schema validation: {details}") from error
        except Exception:
            row.status = "planning_blocked"
            await db.commit()
            raise
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
    version = await _persist_first_version(db, row, user_id, planned)
    return row, version, planned


async def pending_clarification(db, tenant_id, user_id):
    row = await db.scalar(
        select(SoftwarePlanRecord)
        .where(
            SoftwarePlanRecord.tenant_id == tenant_id,
            SoftwarePlanRecord.created_by == user_id,
            SoftwarePlanRecord.status == "awaiting_clarification",
        )
        .order_by(desc(SoftwarePlanRecord.created_at))
    )
    if row is None:
        return None
    item = await _clarification_item(db, row)
    if item is None or item.state != "blocked":
        return None
    payload = json.loads(item.payload_json or "{}")
    return {
        "status": "clarification_required",
        "planId": row.id,
        "questions": payload.get("questions", []),
        "history": payload.get("history", []),
        "prompt": row.prompt,
    }


async def continue_after_clarification(db, row, user_id, answer):
    if row.status != "awaiting_clarification":
        raise PlanConflict("This plan is not waiting for clarification")
    item = await _clarification_item(db, row)
    if item is None or item.state != "blocked":
        raise PlanConflict("No active clarification exists for this plan")
    answer = str(answer or "").strip()
    if not answer:
        raise PlanConflict("Clarification answer cannot be empty")

    payload = json.loads(item.payload_json or "{}")
    questions = payload.get("questions", [])
    history = list(payload.get("history", []))
    history.append({"questions": questions, "answer": answer})
    effective_prompt = _clarified_prompt(row.prompt, history)

    item.state = "running"
    item.payload_json = json.dumps(
        {
            "questions": questions,
            "history": history,
            "originalPrompt": row.prompt,
        }
    )
    row.status = "planning"
    await db.commit()

    mode = planning_mode()
    if mode == PlanningMode.UNAVAILABLE:
        row.status = "awaiting_clarification"
        item.state = "blocked"
        await db.commit()
        raise PlannerUnavailable("planner_unavailable")
    if mode != PlanningMode.LIVE_LLM:
        raise PlanConflict("Clarification continuation requires live planning mode")

    try:
        planned = await _run_live_plan(db, row, row.tenant_id, effective_prompt)
    except PlanningNeedsUserInput as error:
        await _store_clarification(db, row, error.questions, history=history, item=item)
        error.plan_id = row.id
        raise
    except ValidationError as error:
        row.status = "planning_blocked"
        item.state = "failed"
        await db.commit()
        details = "; ".join(
            f"{'.'.join(str(part) for part in entry.get('loc', []))}: {entry.get('msg', 'invalid')}"
            for entry in error.errors()[:8]
        )
        raise PlanningBlocked(f"live plan projection failed schema validation: {details}") from error
    except Exception:
        row.status = "planning_blocked"
        item.state = "failed"
        await db.commit()
        raise

    item.state = "resolved"
    item.blocked_question = None
    item.payload_json = json.dumps(
        {
            "questions": [],
            "history": history,
            "originalPrompt": row.prompt,
        }
    )
    version = await _persist_first_version(
        db,
        row,
        user_id,
        planned,
        revision_request="Planning clarification: " + answer,
    )
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
                "acceptanceCriteria": _interaction_acceptance(req),
                "relatedPlanNodeIds": linked,
                "relatedArtifactIds": [
                    artifact
                    for node in nodes
                    if req.requirement_id in node.linked_requirement_ids
                    for artifact in node.required_artifacts
                ],
                "relatedTestIds": _unique_strings([
                    test
                    for node in nodes
                    if req.requirement_id in node.linked_requirement_ids
                    for test in node.required_tests
                ] + _interaction_test_ids(req)),
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
                "acceptanceCriteria": _unique_strings([
                    criterion
                    for req in analysis.requirements
                    if req.requirement_id in node.linked_requirement_ids
                    for criterion in _interaction_acceptance(req)
                ]),
                "requiredArtifacts": node.required_artifacts,
                "requiredTests": _unique_strings(
                    list(node.required_tests)
                    + [
                        test_id
                        for req in analysis.requirements
                        if req.requirement_id in node.linked_requirement_ids
                        for test_id in _interaction_test_ids(req)
                    ]
                ),
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
        neutral_live_envelope(prompt, analysis.root_objective), analysis, nodes, ledger
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
        "legacyPlannerInvokedForLiveProjection": False,
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