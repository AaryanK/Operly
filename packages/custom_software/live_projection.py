"""Project validated live planning output into the historical SoftwarePlan envelope.

Live recursive planning is the semantic authority.  The compatibility shell in
this module exists only because older APIs/UI still consume ``SoftwarePlan``.  It
must never call another planner or invent product semantics.
"""
from __future__ import annotations

import re


def _unique(values):
    seen = set()
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def neutral_live_envelope(prompt: str, objective: str | None = None) -> dict:
    """Return a schema-only live-mode shell with no guessed product semantics.

    This deliberately does not inspect the request for domains, roles, entities,
    stacks, templates, or application types.  Those values are projected later
    only from validated live requirements/contracts.
    """
    goal = " ".join(str(objective or prompt or "Generated Software").split()).strip()
    name = re.sub(r"[^A-Za-z0-9 &-]+", " ", goal).strip()[:80] or "Generated Software"
    return {
        "schemaVersion": 1,
        "projectName": name,
        "summary": goal,
        "productCategory": "custom software",
        "targetUsers": [],
        "businessDomain": "user-defined",
        "primaryGoal": goal,
        "successCriteria": [
            "all mandatory requirements mapped",
            "all implementation leaves validated",
            "whole-system validation passed",
        ],
        "primaryArchitecture": "live_recursive_requirement_graph",
        "secondaryArchitectures": [],
        "implementationMode": "sandbox_generated",
        "confidence": 0.0,
        "rationale": "Compatibility envelope only; validated live contracts are semantic authority.",
        "roles": [],
        "entities": [],
        "relationships": [],
        "workflows": [],
        "surfaces": [],
        "backendCapabilities": [],
        "integrations": [],
        "design": {
            "family": "minimal",
            "visualPersonality": "implementation-defined from approved requirements",
            "navigationFamily": "implementation-defined",
            "heroFamily": "implementation-defined",
            "typographyPairing": "implementation-defined",
            "typeScale": "responsive",
            "contentDensity": "comfortable",
            "spacingSystem": "responsive",
            "gridSystem": "responsive",
            "surfaceStyle": "implementation-defined",
            "cardStyle": "implementation-defined",
            "ctaStrategy": "task-priority",
            "mediaStrategy": "requirement-driven",
            "motionStrategy": "reduced-motion-safe",
            "responsiveBehavior": "preserve required workflows across viewports",
            "accessibilityGoals": ["keyboard operation", "visible focus", "semantic structure"],
        },
        "runtime": {
            "strategy": "sandbox_generated",
            "reason": "Generated source is executed only through the isolated runner boundary.",
            "primaryPack": None,
            "secondaryPacks": [],
        },
        "securityConstraints": [],
        "unsupportedRequirements": [],
        "risks": [],
        "testRequirements": [],
        "deploymentRequirements": ["isolated preview verification before release"],
        "effectiveRequirements": [],
        "capabilities": [],
        "architectureNodes": [],
        "stack": None,
        "requirementEvidence": [],
        "reusedPrimitives": [],
        "generatedComponents": [],
        "provenance": {
            "semanticAuthority": "validated_recursive_plan",
            "compatibilityEnvelope": "neutral_live",
        },
        "requirementLedger": [],
        "planTree": [],
        "planningMetrics": None,
        "semanticDiff": None,
        "globalValidation": {},
        "planningMode": "live_llm",
        "planningBudget": {},
    }


def project_live_envelope(base: dict, analysis, nodes, ledger: list[dict]) -> dict:
    """Project only validated live output into the compatibility shell."""
    projected = dict(base)

    effective = [
        str(req.normalized_requirement).strip()
        for req in analysis.requirements
        if str(req.normalized_requirement).strip()
    ]
    tests = _unique(test for node in nodes for test in node.required_tests)
    security = _unique(rule for node in nodes for rule in node.security_constraints)

    capabilities = []
    architecture_nodes = []
    for index, node in enumerate(nodes, 1):
        cap_id = f"live_capability_{index:03d}"
        architecture_id = f"live_node_{index:03d}"
        capabilities.append(
            {
                "id": cap_id,
                "category": str(node.node_type or "component"),
                "description": str(node.title or node.objective),
                "requirement": str(node.objective),
                "implementation": "generate_component",
                "status": "planned",
            }
        )
        architecture_nodes.append(
            {
                "id": architecture_id,
                "nodeType": str(node.node_type or "component"),
                "name": str(node.title or node.objective),
                "inputs": list(node.inputs),
                "outputs": list(node.outputs),
                "invariants": list(node.invariants),
                "implementationRequired": True,
            }
        )

    evidence = [
        {
            "requirementId": item["id"],
            "requirement": item["normalizedMeaning"],
            "artifactIds": list(item["relatedArtifactIds"]),
            "testIds": list(item["relatedTestIds"]),
            "status": "planned",
        }
        for item in ledger
    ]

    projected.update(
        {
            # Historical structured views stay empty in live mode unless a
            # validated live projection explicitly exists for them.  The
            # recursive plan tree is the implementation contract.
            "targetUsers": [],
            "roles": [],
            "entities": [],
            "relationships": [],
            "workflows": [],
            "surfaces": [],
            "effectiveRequirements": effective,
            "capabilities": capabilities,
            "architectureNodes": architecture_nodes,
            "backendCapabilities": [item["id"] for item in capabilities],
            "stack": None,
            "requirementEvidence": evidence,
            "reusedPrimitives": [],
            "generatedComponents": [item["id"] for item in capabilities],
            "testRequirements": tests,
            "securityConstraints": security,
            "rationale": (
                "Live architecture is projected only from the validated requirement "
                "ledger and recursive implementation contracts; no second planner "
                "or legacy semantic defaults are used."
            ),
        }
    )
    return projected
