from packages.custom_software.live_planning import (
    PlanningBudget,
    ProposedNode,
    hard_refinement_errors,
    scope_errors,
)


def test_unrequested_database_is_scope_finding_not_hard_refinement_error():
    node = ProposedNode(
        node_id="part-001",
        title="Product records",
        node_type="Contract",
        objective="Track product information",
        responsibilities=["Track product information"],
        linked_requirement_ids=["R-001"],
        inputs=["product details"],
        outputs=["database-backed product record"],
        invariants=["product identity remains stable"],
        failure_cases=["invalid product data is rejected"],
        required_artifacts=["product record contract"],
        required_tests=["verify product records can be created and retrieved"],
    )
    linked = [{
        "requirement_id": "R-001",
        "source_excerpt": "keep track of products",
        "normalized_requirement": "Track product information",
    }]

    assert scope_errors(node, linked) == ["unjustified scope expansion: database"]
    assert hard_refinement_errors(
        [node], {"R-001"}, [], PlanningBudget(), {"inventory_system_root"}
    ) == []
