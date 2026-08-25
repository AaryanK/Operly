from packages.software_projects.planning.graph_planning import (
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


def test_structural_repair_attaches_financial_requirements_to_semantic_owners():
    analysis = RequirementsAnalysis.model_validate({
        "root_objective": "Build a financial planner with projections and charts.",
        "requirements": [
            {"requirement_id": "R-001", "source_excerpt": "enter revenue and costs", "normalized_requirement": "Enter revenue and cost assumptions.", "category": "input", "priority": "High", "acceptance_criteria": ["Inputs can be edited."]},
            {"requirement_id": "R-002", "source_excerpt": "calculate margin", "normalized_requirement": "Calculate gross margin and cash flow projections.", "category": "calculation", "priority": "High", "acceptance_criteria": ["Calculations update from assumptions."]},
            {"requirement_id": "R-003", "source_excerpt": "show charts", "normalized_requirement": "Display financial projections as charts and summary cards.", "category": "interface", "priority": "High", "acceptance_criteria": ["Charts show calculated projections."]},
        ],
        "questions_requiring_user_input": [],
    })
    graph = CapabilityGraph.model_validate({"nodes": [
        {
            "node_id": "assumptions",
            "title": "Assumption input manager",
            "objective": "Collect revenue and cost assumptions.",
            "responsibility": "Validate editable financial inputs.",
            "requirement_ids": ["R-001"],
            "dependencies": [],
            "inputs": ["revenue", "costs"],
            "outputs": ["validated assumptions"],
            "invariants": ["Inputs remain numeric."],
            "failure_cases": ["Invalid assumptions are rejected."],
        },
        {
            "node_id": "projection_engine",
            "title": "Financial calculation engine",
            "objective": "Calculate margins and cash flow projections.",
            "responsibility": "Derive financial metrics from validated assumptions.",
            "requirement_ids": ["R-001"],
            "dependencies": ["assumptions"],
            "inputs": ["validated assumptions"],
            "outputs": ["calculated projections"],
            "invariants": ["Metrics are deterministic."],
            "failure_cases": ["Missing inputs prevent calculation."],
        },
        {
            "node_id": "dashboard",
            "title": "Projection dashboard renderer",
            "objective": "Render charts and financial summary cards.",
            "responsibility": "Present calculated projections.",
            "requirement_ids": ["R-001"],
            "dependencies": ["projection_engine"],
            "inputs": ["calculated projections"],
            "outputs": ["charts", "summary cards"],
            "invariants": ["Displayed values match calculations."],
            "failure_cases": ["Unavailable projections show an empty state."],
        },
    ]})

    repaired = _deterministic_graph_repair(graph, analysis)
    by_id = {node.node_id: node for node in repaired.nodes}

    assert "R-002" in by_id["projection_engine"].requirement_ids
    assert "R-003" in by_id["dashboard"].requirement_ids
    assert all(not node.node_id.startswith("coverage_") for node in repaired.nodes)
