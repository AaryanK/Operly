"""Project live recursive planning output into the legacy SoftwarePlan envelope.

The recursive planner is the semantic authority in live mode.  This module keeps
legacy presentation fields from inventing roles, entities, workflows, or stack
choices that were never present in the validated live contracts.
"""
from __future__ import annotations


def _unique(values):
    seen = set()
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def project_live_envelope(base: dict, analysis, nodes, ledger: list[dict]) -> dict:
    """Return a compatibility envelope derived only from validated live output.

    ``build_software_plan`` is still used to satisfy the historical SoftwarePlan
    shape, but none of its guessed semantic fields are allowed to survive into a
    live plan.  The authoritative implementation contract is the requirement
    ledger plus the validated recursive plan tree.
    """
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
            # These old structured views are not semantic authorities in live
            # mode. Empty is safer than fabricated defaults. The recursive tree
            # carries the actual roles/data/workflows when the request needs them.
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
                "ledger and recursive implementation contracts; legacy planner "
                "defaults are not semantic input."
            ),
        }
    )
    return projected
