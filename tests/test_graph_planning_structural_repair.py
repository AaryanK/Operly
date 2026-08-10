from packages.custom_software.graph_planning import (
    CapabilityGraph,
    RequirementsAnalysis,
    _deterministic_graph_repair,
    _graph_errors,
)


def test_structural_repair_removes_dangling_dependencies_and_owns_missing_requirements():
    analysis = RequirementsAnalysis.model_validate({
        "root_objective": "Collect and rank bakery feedback.",
        "requirements": [
            {"requirement_id": "R-001", "source_excerpt": "collect feedback", "normalized_requirement": "Collect feedback.", "category": "behavior", "priority": "High", "acceptance_criteria": ["Feedback can be submitted."]},
            {"requirement_id": "R-002", "source_excerpt": "rank feedback", "normalized_requirement": "Rank feedback by votes.", "category": "behavior", "priority": "High", "acceptance_criteria": ["Feedback is ordered by votes."]},
        ],
        "questions_requiring_user_input": [],
    })
    graph = CapabilityGraph.model_validate({"nodes": [{
        "node_id": "feedback",
        "title": "Feedback",
        "objective": "Collect feedback.",
        "responsibility": "Collect feedback.",
        "requirement_ids": ["R-001"],
        "dependencies": ["invented_storage_node"],
        "inputs": ["feedback"],
        "outputs": ["stored feedback"],
        "invariants": ["Feedback has an identity."],
        "failure_cases": ["Invalid feedback is rejected."],
    }]})

    repaired = _deterministic_graph_repair(graph, analysis)

    assert _graph_errors(repaired, analysis) == []
    assert repaired.nodes[0].dependencies == []
    assert {requirement_id for node in repaired.nodes for requirement_id in node.requirement_ids} == {"R-001", "R-002"}
