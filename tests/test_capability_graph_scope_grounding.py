from packages.custom_software.graph_planning import (
    CapabilityGraph,
    CapabilityGraphNode,
    _graph_errors,
)
from packages.custom_software.live_planning import AnalystRequirement, RequirementsAnalysis


def _analysis(
    *,
    source_excerpt: str,
    normalized_requirement: str,
    category: str,
    acceptance_criteria: list[str],
    explicit_terms: list[str] | None = None,
) -> RequirementsAnalysis:
    return RequirementsAnalysis(
        root_objective="Build the requested software behavior.",
        requirements=[
            AnalystRequirement(
                requirement_id="R-004",
                source_excerpt=source_excerpt,
                normalized_requirement=normalized_requirement,
                category=category,
                priority="mandatory",
                acceptance_criteria=acceptance_criteria,
                explicit_terms=explicit_terms or [],
            )
        ],
    )


def _database_node(node_id: str = "requirement_r_004") -> CapabilityGraphNode:
    return CapabilityGraphNode(
        node_id=node_id,
        title="Database-backed attendance records",
        objective="Store accepted attendance events in a database.",
        responsibility="Persist attendance records in the database.",
        requirement_ids=["R-004"],
        dependencies=[],
        inputs=["accepted attendance event"],
        outputs=["stored attendance record"],
        invariants=["Successful writes remain available for later reads."],
        failure_cases=["Do not report success if the record cannot be stored."],
        security_constraints=[],
        persistence_behavior=["Persist accepted attendance records."],
    )


def test_database_scope_is_grounded_by_persistence_requirement() -> None:
    analysis = _analysis(
        source_excerpt="Keep accepted clock events for later use.",
        normalized_requirement="Persist attendance records and timestamps securely.",
        category="persistence",
        acceptance_criteria=["Accepted records remain available after a reload."],
    )

    findings = _graph_errors(CapabilityGraph(nodes=[_database_node()]), analysis)

    assert not any("unjustified scope expansion: database" in finding for finding in findings)


def test_database_scope_remains_rejected_for_unrelated_requirement() -> None:
    analysis = _analysis(
        source_excerpt="Show a welcome message.",
        normalized_requirement="Render a welcome message to the user.",
        category="interface",
        acceptance_criteria=["The welcome message is visible."],
    )

    findings = _graph_errors(CapabilityGraph(nodes=[_database_node()]), analysis)

    assert any("unjustified scope expansion: database" in finding for finding in findings)


def test_review_gap_inherits_database_grounding_from_linked_requirement() -> None:
    analysis = _analysis(
        source_excerpt="Store attendance data securely.",
        normalized_requirement="Retain clock-in and clock-out history for later retrieval.",
        category="persistence",
        acceptance_criteria=["Attendance history survives subsequent reads."],
        explicit_terms=["PostgreSQL"],
    )

    findings = _graph_errors(
        CapabilityGraph(nodes=[_database_node("operly_review_gap_2f8008c8")]),
        analysis,
    )

    assert not any("unjustified scope expansion: database" in finding for finding in findings)
