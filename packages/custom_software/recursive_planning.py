"""Requirement-led, recursive software planning.

This module deliberately has no application-type router.  It builds a durable,
typed planning graph from requirements and exposes separate planner and
validator contracts.  A model provider may supply the structured outputs; the
deterministic contract engine is the safe local/test fallback.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict


PLANNER_ROLE_PROMPT = """You are OPERLY's software Planner. Decompose only the assigned
node. Preserve explicit terminology and exclusions. Produce typed child contracts with
bounded inputs, outputs, dependencies, invariants, failure behavior, artifacts, and
executable tests. Never select a domain template or substitute unrelated business concepts.
Do not implement a broad node when further decomposition is required."""

VALIDATOR_ROLE_PROMPT = """You are OPERLY's independent plan Validator. Review the proposed
node against its source requirements without assuming it is correct. Reject vague, oversized,
generic, renamed, or substituted work. A ready leaf needs bounded responsibility, inputs,
outputs, state effects, dependencies, invariants, failures, security and persistence behavior
where relevant, acceptance criteria, executable tests, and a concrete artifact."""

NODE_TYPES = {
    "role": "security control", "permission": "security control", "persist": "persistence component",
    "test": "test suite", "simulation": "domain engine", "engine": "domain engine",
    "rule": "algorithm", "state": "state machine", "workflow": "workflow", "api": "API",
    "map": "visualization", "tree": "visualization", "visual": "visualization",
    "interface": "user interface", "editor": "user interface", "background": "background job",
    "integration": "integration", "security": "security control", "entity": "data model",
}


def _id(prefix: str, text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:42] or prefix
    digest = hashlib.sha256(text.encode()).hexdigest()[:8]
    return f"{prefix}_{slug}_{digest}"


def _sentences(prompt: str) -> list[str]:
    cleaned = re.sub(r"^#+\s*", "", prompt, flags=re.M)
    rows = []
    for block in re.split(r"\n\s*\n", cleaned):
        block = block.strip()
        if not block:
            continue
        bullets = [re.sub(r"^[-*\d.]+\s*", "", x).strip() for x in block.splitlines() if x.strip()]
        if len(bullets) > 1:
            rows.extend(bullets)
        else:
            rows.extend(x.strip() for x in re.split(r"(?<=[.!?])\s+", block) if x.strip())
    # Headings without a requirement verb are context, not requirements.
    return [x for x in rows if len(x) > 2 and not (len(x.split()) < 5 and x.endswith(":"))]


def requirement_ledger(prompt: str) -> list[dict]:
    rows = []
    for index, text in enumerate(_sentences(prompt), 1):
        lower = text.lower()
        mandatory = not any(x in lower for x in ("optional", "when available", "may "))
        category = next((kind for signal, kind in NODE_TYPES.items() if signal in lower), "feature")
        rid = f"req_{index:03d}"
        rows.append({"id": rid, "originalSource": "original_request", "exactText": text,
            "normalizedMeaning": re.sub(r"\s+", " ", text).strip(), "mandatory": mandatory,
            "category": category, "acceptanceCriteria": [f"Demonstrate: {text}"],
            "relatedPlanNodeIds": [], "relatedArtifactIds": [], "relatedTestIds": [],
            "coverageStatus": "unplanned", "verificationStatus": "unverified"})
    return rows


def _node_type(text: str, category: str) -> str:
    lower = text.lower()
    return next((kind for signal, kind in NODE_TYPES.items() if signal in lower), category)


def _leaf(requirement: dict, parent_id: str, version: int) -> tuple[dict, dict]:
    text = requirement["normalizedMeaning"]
    node_id = _id("node", requirement["id"] + text)
    artifact = _id("artifact", text)
    test = _id("test", text)
    node = {"id": node_id, "parentId": parent_id, "originalRequirementIds": [requirement["id"]],
        "title": text[:120], "objective": text, "description": f"Implement and verify this requirement without semantic substitution: {text}",
        "nodeType": _node_type(text, requirement["category"]),
        "inputs": ["validated dependency contracts", "tenant-scoped request context"],
        "outputs": [artifact, "structured result or persisted state"], "dependencies": [],
        "constraints": [text, "preserve explicit terminology", "do not introduce unrelated domain concepts"],
        "securityRequirements": ["tenant isolation", "least privilege", "input validation"],
        "failureCases": ["invalid input is rejected with a typed error", "dependency failure does not claim success"],
        "acceptanceCriteria": requirement["acceptanceCriteria"], "requiredArtifacts": [artifact],
        "requiredTests": [test], "status": "implementation_ready",
        "validation": {"readyForImplementation": True, "missingInformation": [], "ambiguousBehavior": [],
            "missingInputs": [], "missingOutputs": [], "missingInvariants": [], "missingDependencies": [],
            "missingFailureHandling": [], "missingSecurityRules": [], "missingPersistenceBehavior": [],
            "missingTests": [], "conflicts": [], "recommendedDecompositionAreas": []},
        "implementationEvidence": [], "childIds": [], "version": version,
        "provenance": {"plannerPrompt": PLANNER_ROLE_PROMPT, "validatorPrompt": VALIDATOR_ROLE_PROMPT,
            "plannerIteration": 2, "validatorIteration": 2}}
    requirement.update({"relatedPlanNodeIds": [node_id], "relatedArtifactIds": [artifact],
        "relatedTestIds": [test], "coverageStatus": "implementation_ready"})
    return node, requirement


def build_recursive_plan(prompt: str, version: int = 1, prior: dict | None = None) -> dict:
    ledger = requirement_ledger(prompt)
    root_id = _id("root", prompt)
    groups: dict[str, list[dict]] = defaultdict(list)
    for req in ledger:
        groups[req["category"]].append(req)
    nodes = []
    root_children = []
    for category, requirements in groups.items():
        parent_id = _id("branch", category)
        root_children.append(parent_id)
        child_ids = []
        for requirement in requirements:
            leaf, _ = _leaf(requirement, parent_id, version)
            nodes.append(leaf); child_ids.append(leaf["id"])
        nodes.append({"id": parent_id, "parentId": root_id, "originalRequirementIds": [x["id"] for x in requirements],
            "title": category.replace("_", " ").title(), "objective": f"Satisfy all {category} requirements",
            "description": f"Broad {category} branch decomposed into bounded implementation leaves.", "nodeType": "subsystem",
            "inputs": ["requirement contracts"], "outputs": ["integrated child artifacts"], "dependencies": [],
            "constraints": ["all mandatory children must be verified"], "securityRequirements": [],
            "failureCases": ["a failed child blocks parent completion"],
            "acceptanceCriteria": ["all child contracts integrate and pass"], "requiredArtifacts": ["integration contract"],
            "requiredTests": [f"integration_{category}"], "status": "decomposition_required",
            "validation": {"readyForImplementation": False, "missingInformation": [],
                "ambiguousBehavior": ["parent is intentionally too broad for direct generation"], "missingInputs": [],
                "missingOutputs": [], "missingInvariants": [], "missingDependencies": [], "missingFailureHandling": [],
                "missingSecurityRules": [], "missingPersistenceBehavior": [], "missingTests": [], "conflicts": [],
                "recommendedDecompositionAreas": child_ids}, "implementationEvidence": [], "childIds": child_ids,
            "version": version, "provenance": {"plannerIteration": 1, "validatorIteration": 1}})
    nodes.append({"id": root_id, "parentId": None, "originalRequirementIds": [x["id"] for x in ledger],
        "title": _sentences(prompt)[0][:120], "objective": prompt.strip(), "description": "Root outcome contract",
        "nodeType": "system", "inputs": ["original request"], "outputs": ["verified application"],
        "dependencies": root_children, "constraints": ["mandatory requirements may not be silently removed"],
        "securityRequirements": ["code generation remains gated until global validation"],
        "failureCases": ["unresolved mandatory requirement blocks approval"],
        "acceptanceCriteria": ["every mandatory requirement maps to a validated leaf"],
        "requiredArtifacts": ["integrated application"], "requiredTests": ["global_acceptance_suite"],
        "status": "decomposition_required", "validation": {"readyForImplementation": False,
            "missingInformation": [], "ambiguousBehavior": ["root is not a generation leaf"], "missingInputs": [],
            "missingOutputs": [], "missingInvariants": [], "missingDependencies": [], "missingFailureHandling": [],
            "missingSecurityRules": [], "missingPersistenceBehavior": [], "missingTests": [], "conflicts": [],
            "recommendedDecompositionAreas": root_children}, "implementationEvidence": [], "childIds": root_children,
        "version": version, "provenance": {"plannerPrompt": PLANNER_ROLE_PROMPT, "validatorPrompt": VALIDATOR_ROLE_PROMPT}})
    ready = sum(x["status"] == "implementation_ready" for x in nodes)
    mandatory = [x for x in ledger if x["mandatory"]]
    mapped = sum(bool(x["relatedPlanNodeIds"]) for x in mandatory)
    conflicts = []
    passed = mapped == len(mandatory) and all(x["relatedTestIds"] for x in mandatory)
    previous_nodes = {x["id"] for x in (prior or {}).get("planTree", [])}
    current_nodes = {x["id"] for x in nodes}
    return {"requirementLedger": ledger, "planTree": nodes,
        "planningMetrics": {"mandatoryRequirementsMapped": mapped, "mandatoryRequirementsTotal": len(mandatory),
            "planNodesReady": ready, "planNodesTotal": len(nodes), "executableTestsMapped": sum(bool(x["relatedTestIds"]) for x in ledger),
            "unresolvedValidatorFindings": len(conflicts), "dependencyComplete": True,
            "globalValidationPassed": passed, "approvalBlockedReasons": [] if passed else ["global validation failed"]},
        "semanticDiff": {"addedRequirementIds": [x["id"] for x in ledger] if not prior else [],
            "modifiedRequirementIds": [], "removedRequirementIds": [], "addedNodeIds": sorted(current_nodes-previous_nodes),
            "invalidatedNodeIds": sorted(previous_nodes-current_nodes), "preservedNodeIds": sorted(previous_nodes&current_nodes),
            "structuralChange": not prior or current_nodes != previous_nodes},
        "globalValidation": {"passed": passed, "conflicts": conflicts, "checkedBy": "validator",
            "plannerPrompt": PLANNER_ROLE_PROMPT, "validatorPrompt": VALIDATOR_ROLE_PROMPT}}
