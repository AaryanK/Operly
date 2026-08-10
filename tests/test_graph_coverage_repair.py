import asyncio

from packages.custom_software.live_planning import StructuredModelResult
from packages.custom_software.planning_orchestrator import RecursiveRepairPlanningOrchestrator


REQUIREMENTS = {
    "root_objective": "Create and compare scenarios.",
    "requirements": [
        {
            "requirement_id": "R-001",
            "source_excerpt": "create scenarios",
            "normalized_requirement": "Allow the user to create scenarios.",
            "category": "behavior",
            "priority": "High",
            "acceptance_criteria": ["A scenario can be created."],
        },
        {
            "requirement_id": "R-002",
            "source_excerpt": "compare two scenarios side by side",
            "normalized_requirement": "Compare two scenarios side by side.",
            "category": "interface",
            "priority": "High",
            "acceptance_criteria": ["Two selected scenarios are visible side by side."],
        },
        {
            "requirement_id": "R-003",
            "source_excerpt": "persist everything",
            "normalized_requirement": "Persist scenario state for later retrieval.",
            "category": "persistence",
            "priority": "High",
            "acceptance_criteria": ["Saved scenarios are available after returning later."],
        },
    ],
    "questions_requiring_user_input": [],
}


INCOMPLETE_GRAPH = {
    "nodes": [
        {
            "node_id": "SCENARIOS",
            "title": "Scenario state",
            "objective": "Maintain scenario definitions.",
            "responsibility": "Create and expose scenario state.",
            "requirement_ids": ["R-001"],
            "dependencies": [],
            "inputs": ["scenario changes"],
            "outputs": ["current scenarios"],
            "invariants": ["Each scenario has a stable identity."],
            "failure_cases": ["Invalid changes do not alter existing scenario state."],
            "security_constraints": [],
            "persistence_behavior": ["Scenario state remains available when the user returns."],
        },
        {
            "node_id": "COMPARE",
            "title": "Scenario comparison",
            "objective": "Compare selected scenarios.",
            "responsibility": "Render two selected scenarios side by side.",
            "requirement_ids": ["R-002"],
            "dependencies": ["SCENARIOS", "MODEL_INVENTED_MISSING_NODE"],
            "inputs": ["two selected scenarios"],
            "outputs": ["side-by-side comparison"],
            "invariants": ["Comparison does not mutate either scenario."],
            "failure_cases": ["Missing selections produce an explicit validation state."],
            "security_constraints": [],
            "persistence_behavior": [],
        },
    ]
}


APPROVED_REVIEW = {
    "approved": True,
    "missing_requirement_ids": [],
    "dependency_issues": [],
    "semantic_gaps": [],
    "unnecessary_implementation_details": [],
    "user_questions": [],
    "reasoning_summary": "The capability graph is complete and coherent.",
}


class CoverageClient:
    provider = "fake"
    model_id = "fake-coverage"

    def __init__(self):
        self.calls = []

    async def generate_structured(self, *, role, context, output_schema, request_id, timeout_seconds, attempt=1):
        schema_name = output_schema.__name__
        self.calls.append((role, schema_name))
        if role == "requirements_analyst":
            payload = REQUIREMENTS
        elif role == "planner" and schema_name == "CapabilityGraph":
            payload = INCOMPLETE_GRAPH
        elif role == "planner" and schema_name == "CoveragePatch":
            assert context.constraints["missing_requirement_ids"] == ["R-003"]
            payload = {
                "assignments": [
                    {"requirement_id": "R-003", "node_ids": ["SCENARIOS"]}
                ]
            }
        elif role == "global_validator":
            assert context.constraints["deterministic_findings"] == []
            payload = APPROVED_REVIEW
        else:
            raise AssertionError(f"unexpected call: {role} {schema_name}")

        validated = output_schema.model_validate(payload)
        return StructuredModelResult(
            provider=self.provider,
            model_id=self.model_id,
            request_id=request_id,
            attempt=attempt,
            latency_ms=1,
            input_tokens=50,
            output_tokens=50,
            structured_output=validated.model_dump(mode="json"),
            raw_response="{}",
            context_digest=context.digest(),
        )


def test_missing_requirement_links_use_small_coverage_patch_not_graph_regeneration():
    client = CoverageClient()
    orchestrator = RecursiveRepairPlanningOrchestrator(client)

    outcome = asyncio.run(orchestrator.run("Build Scenario Forge."))

    assert client.calls == [
        ("requirements_analyst", "RequirementsAnalysis"),
        ("planner", "CapabilityGraph"),
        ("planner", "CoveragePatch"),
        ("global_validator", "GraphReview"),
    ]
    assert orchestrator.budget.calls == 4
    assert outcome["coverage_repair_rounds"] == 1
    assert outcome["graph_repair_rounds"] == 0
    nodes = {node.node_id: node for node in outcome["nodes"]}
    assert nodes["SCENARIOS"].linked_requirement_ids == ["R-001", "R-003"]
    assert nodes["COMPARE"].linked_requirement_ids == ["R-002"]
    assert nodes["COMPARE"].dependencies == ["SCENARIOS"]
