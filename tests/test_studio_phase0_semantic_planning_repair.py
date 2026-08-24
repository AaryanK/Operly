import asyncio

from packages.custom_software.graph_coverage import CoverageAwareGraphPlanningOrchestrator
from packages.custom_software.live_planning import StructuredModelResult


REQUIREMENTS = {
    "root_objective": "Build an employee attendance system.",
    "requirements": [
        {
            "requirement_id": "R-001",
            "source_excerpt": "employees can clock in",
            "normalized_requirement": "Allow an employee to clock in.",
            "category": "behavior",
            "priority": "mandatory",
            "acceptance_criteria": ["A valid employee can create a clock-in event."],
        },
        {
            "requirement_id": "R-002",
            "source_excerpt": "admins can review attendance in a dashboard",
            "normalized_requirement": "Provide an admin attendance dashboard.",
            "category": "interface",
            "priority": "mandatory",
            "acceptance_criteria": ["An authorized admin can review attendance records."],
        },
    ],
    "questions_requiring_user_input": [],
}


CLOCK_IN_OVERLOADED = {
    "nodes": [
        {
            "node_id": "CLOCK_IN",
            "title": "Employee clock in",
            "objective": "Record employee clock-in events.",
            "responsibility": "Validate and record an employee clock-in event.",
            "requirement_ids": ["R-001", "R-002"],
            "dependencies": [],
            "inputs": ["employee identity", "clock-in request"],
            "outputs": ["recorded clock-in event"],
            "invariants": ["an accepted clock-in is persisted exactly once"],
            "failure_cases": ["invalid employee or duplicate clock-in is rejected"],
            "security_constraints": ["employee identity is validated"],
            "persistence_behavior": ["persist the clock-in event"],
        }
    ]
}


FAILED_REVIEW = {
    "approved": False,
    "missing_requirement_ids": ["R-002"],
    "dependency_issues": [],
    "semantic_gaps": ["Missing authenticated admin attendance dashboard capability"],
    "unnecessary_implementation_details": [],
    "user_questions": [],
    "reasoning_summary": "The clock-in endpoint claims dashboard coverage without implementing it.",
}


APPROVED_REVIEW = {
    "approved": True,
    "missing_requirement_ids": [],
    "dependency_issues": [],
    "semantic_gaps": [],
    "unnecessary_implementation_details": [],
    "user_questions": [],
    "reasoning_summary": "The graph has direct executable owners for both requirements.",
}


CORRECT_REPAIR = {
    "nodes": [
        {
            "node_id": "CLOCK_IN",
            "title": "Employee clock in",
            "objective": "Record employee clock-in events.",
            "responsibility": "Validate and record an employee clock-in event.",
            "requirement_ids": ["R-001"],
            "dependencies": [],
            "inputs": ["employee identity", "clock-in request"],
            "outputs": ["recorded clock-in event"],
            "invariants": ["an accepted clock-in is persisted exactly once"],
            "failure_cases": ["invalid employee or duplicate clock-in is rejected"],
            "security_constraints": ["employee identity is validated"],
            "persistence_behavior": ["persist the clock-in event"],
        },
        {
            "node_id": "ADMIN_DASHBOARD",
            "title": "Admin attendance dashboard",
            "objective": "Let an authorized admin review attendance records.",
            "responsibility": "Render and query attendance records for an authorized admin.",
            "requirement_ids": ["R-002"],
            "dependencies": ["CLOCK_IN"],
            "inputs": ["authenticated admin", "attendance query"],
            "outputs": ["attendance records view"],
            "invariants": ["only authorized admins can access attendance records"],
            "failure_cases": ["unauthorized access is rejected"],
            "security_constraints": ["require admin authorization"],
            "persistence_behavior": [],
        },
    ]
}


class SemanticRepairClient:
    provider = "fake"
    model_id = "semantic-repair"

    def __init__(self, repaired_graph):
        self.repaired_graph = repaired_graph
        self.calls = []
        self.review_count = 0

    async def generate_structured(
        self,
        *,
        role,
        context,
        output_schema,
        request_id,
        timeout_seconds,
        attempt=1,
    ):
        schema_name = output_schema.__name__
        self.calls.append((role, schema_name))
        if role == "requirements_analyst":
            payload = REQUIREMENTS
        elif role == "planner" and schema_name == "CapabilityGraph":
            if context.current_contract.get("graph"):
                repair_contract = context.previous_findings[-1]["repair_contract"]
                assert repair_contract["resolve_every_review_finding"] is True
                assert repair_contract["may_add_split_replace_or_remove_nodes"] is True
                assert repair_contract["do_not_preserve_old_requirement_links_only_for_coverage"] is True
                payload = self.repaired_graph
            else:
                payload = CLOCK_IN_OVERLOADED
        elif role == "global_validator":
            self.review_count += 1
            if self.review_count == 1:
                payload = FAILED_REVIEW
            else:
                nodes = {
                    item["node_id"]: item
                    for item in context.current_contract["nodes"]
                }
                assert nodes["CLOCK_IN"]["requirement_ids"] == ["R-001"]
                assert any(
                    item["requirement_ids"] == ["R-002"]
                    for item in context.current_contract["nodes"]
                )
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
            input_tokens=20,
            output_tokens=20,
            structured_output=validated.model_dump(mode="json"),
            raw_response="{}",
            context_digest=context.digest(),
        )


def test_semantic_repair_can_remove_false_requirement_links_instead_of_readding_them():
    client = SemanticRepairClient(CORRECT_REPAIR)
    outcome = asyncio.run(
        CoverageAwareGraphPlanningOrchestrator(client).run("Build employee attendance.")
    )

    nodes = {node.node_id: node for node in outcome["nodes"]}
    assert nodes["CLOCK_IN"].linked_requirement_ids == ["R-001"]
    assert nodes["ADMIN_DASHBOARD"].linked_requirement_ids == ["R-002"]
    assert outcome["graph_repair_rounds"] == 1
    assert client.calls == [
        ("requirements_analyst", "RequirementsAnalysis"),
        ("planner", "CapabilityGraph"),
        ("global_validator", "GraphReview"),
        ("planner", "CapabilityGraph"),
        ("global_validator", "GraphReview"),
    ]


def test_semantic_repair_restores_dropped_mandatory_requirement_as_explicit_leaf():
    repair_without_dashboard = {
        "nodes": [CORRECT_REPAIR["nodes"][0]],
    }
    client = SemanticRepairClient(repair_without_dashboard)
    outcome = asyncio.run(
        CoverageAwareGraphPlanningOrchestrator(client).run("Build employee attendance.")
    )

    owners = [
        node
        for node in outcome["nodes"]
        if "R-002" in node.linked_requirement_ids
    ]
    assert len(owners) == 1
    assert owners[0].node_id.startswith("coverage_r_002")
    assert "admin attendance dashboard" in owners[0].responsibilities[0].lower()
    # No extra model call is spent merely restoring syntactic coverage after the
    # semantic repair turn; the final validator still decides whether it is enough.
    assert client.calls == [
        ("requirements_analyst", "RequirementsAnalysis"),
        ("planner", "CapabilityGraph"),
        ("global_validator", "GraphReview"),
        ("planner", "CapabilityGraph"),
        ("global_validator", "GraphReview"),
    ]
