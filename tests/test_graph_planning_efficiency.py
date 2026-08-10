import asyncio

from packages.custom_software.live_planning import StructuredModelResult
from packages.custom_software.planning_orchestrator import RecursiveRepairPlanningOrchestrator


REQUIREMENTS = {
    "root_objective": "Compare options on a visual decision board.",
    "requirements": [
        {
            "requirement_id": "R-001",
            "source_excerpt": "create options",
            "normalized_requirement": "Allow the user to create options.",
            "category": "behavior",
            "priority": "High",
            "acceptance_criteria": ["An option can be created and retrieved."],
        },
        {
            "requirement_id": "R-002",
            "source_excerpt": "rank the options",
            "normalized_requirement": "Rank options using user supplied criteria.",
            "category": "behavior",
            "priority": "High",
            "acceptance_criteria": ["The board returns an ordered option ranking."],
        },
        {
            "requirement_id": "R-003",
            "source_excerpt": "show option cards",
            "normalized_requirement": "Display the ranked options as cards.",
            "category": "interface",
            "priority": "High",
            "acceptance_criteria": ["Every ranked option is visible as one card."],
        },
    ],
    "questions_requiring_user_input": [],
}


VALID_GRAPH = {
    "nodes": [
        {
            "node_id": "OPTION_STATE",
            "title": "Option state",
            "objective": "Maintain the user's decision options.",
            "responsibility": "Accept option changes and expose the current option set.",
            "requirement_ids": ["R-001"],
            "dependencies": [],
            "inputs": ["option change"],
            "outputs": ["current option set"],
            "invariants": ["Each stored option has a stable identity."],
            "failure_cases": ["Invalid option changes are rejected without changing existing state."],
            "security_constraints": [],
            "persistence_behavior": ["The option set remains available when the user returns later."],
        },
        {
            "node_id": "RANKING",
            "title": "Option ranking",
            "objective": "Rank the current options from supplied criteria.",
            "responsibility": "Calculate and return the ordered option ranking.",
            "requirement_ids": ["R-002"],
            "dependencies": ["OPTION_STATE"],
            "inputs": ["current option set and criteria"],
            "outputs": ["ordered option ranking"],
            "invariants": ["The same option values and criteria produce the same ranking."],
            "failure_cases": ["Incomplete criterion values produce a bounded validation failure."],
            "security_constraints": [],
            "persistence_behavior": [],
        },
        {
            "node_id": "BOARD_VIEW",
            "title": "Decision board view",
            "objective": "Show the ranked options as interactive cards.",
            "responsibility": "Render one card for each ranked option.",
            "requirement_ids": ["R-003"],
            "dependencies": ["RANKING"],
            "inputs": ["ordered option ranking"],
            "outputs": ["interactive option cards"],
            "invariants": ["Every ranked option is represented exactly once."],
            "failure_cases": ["An empty ranking renders an explicit empty state."],
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
    "reasoning_summary": "All mandatory requirements are covered by a coherent capability graph.",
}


class GraphClient:
    provider = "fake"
    model_id = "fake-graph"

    def __init__(self, repair=False):
        self.calls = []
        self.repair = repair
        self.planner_calls = 0

    async def generate_structured(self, *, role, context, output_schema, request_id, timeout_seconds, attempt=1):
        self.calls.append(role)
        if role == "requirements_analyst":
            payload = REQUIREMENTS
        elif role == "planner":
            self.planner_calls += 1
            payload = VALID_GRAPH
            if self.repair and self.planner_calls == 1:
                payload = {
                    "nodes": [
                        {
                            **VALID_GRAPH["nodes"][0],
                            "outputs": [],
                        },
                        *VALID_GRAPH["nodes"][1:],
                    ]
                }
        elif role == "global_validator":
            deterministic = list((context.constraints or {}).get("deterministic_findings") or [])
            payload = APPROVED_REVIEW if not deterministic else {
                **APPROVED_REVIEW,
                "approved": False,
                "semantic_gaps": ["Resolve deterministic graph findings before approval."],
                "reasoning_summary": "The graph requires one bounded repair.",
            }
        else:
            raise AssertionError(f"unexpected recursive planning role: {role}")

        validated = output_schema.model_validate(payload)
        return StructuredModelResult(
            provider=self.provider,
            model_id=self.model_id,
            request_id=request_id,
            attempt=attempt,
            latency_ms=1,
            input_tokens=100,
            output_tokens=100,
            structured_output=validated.model_dump(mode="json"),
            raw_response="{}",
            context_digest=context.digest(),
        )


def test_normal_graph_plan_uses_three_model_calls_and_no_per_node_llm_loop():
    client = GraphClient()
    orchestrator = RecursiveRepairPlanningOrchestrator(client)

    outcome = asyncio.run(orchestrator.run("Build a visual decision board."))

    assert client.calls == ["requirements_analyst", "planner", "global_validator"]
    assert orchestrator.budget.calls == 3
    assert orchestrator.budget.tokens == 600
    assert outcome["planning_engine"] == "dynamic_capability_graph_v1"
    assert len(outcome["nodes"]) == 3
    assert set(outcome["validations"]) == {"OPTION_STATE", "RANKING", "BOARD_VIEW"}
    assert "validator" not in client.calls
    assert "requirement_partitioner" not in client.calls
    assert "contract_expander" not in client.calls


def test_graph_level_repair_is_bounded_to_five_calls():
    client = GraphClient(repair=True)
    orchestrator = RecursiveRepairPlanningOrchestrator(client)

    outcome = asyncio.run(orchestrator.run("Build a visual decision board."))

    assert client.calls == [
        "requirements_analyst",
        "planner",
        "global_validator",
        "planner",
        "global_validator",
    ]
    assert orchestrator.budget.calls == 5
    assert outcome["graph_repair_rounds"] == 1
    assert outcome["global"].approved is True
