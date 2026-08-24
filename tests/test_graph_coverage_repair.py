import asyncio

from packages.custom_software.graph_coverage import CoverageAwareGraphPlanningOrchestrator
from packages.custom_software.live_planning import FailureClass, StructuredModelResult
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


ATTENDANCE_REQUIREMENTS = {
    "root_objective": "Build an employee QR attendance system.",
    "requirements": [
        {
            "requirement_id": "R-001",
            "source_excerpt": "Employees clock in",
            "normalized_requirement": "Allow an employee to clock in.",
            "category": "behavior",
            "priority": "mandatory",
            "acceptance_criteria": ["A valid employee can clock in once for the active attendance period."],
        },
        {
            "requirement_id": "R-002",
            "source_excerpt": "Employees clock out",
            "normalized_requirement": "Allow an employee to clock out after clocking in.",
            "category": "behavior",
            "priority": "mandatory",
            "acceptance_criteria": ["A clocked-in employee can clock out and the transition is recorded."],
        },
        {
            "requirement_id": "R-003",
            "source_excerpt": "scan a QR code using the camera",
            "normalized_requirement": "Use a camera QR scan to select the intended attendance action.",
            "category": "interface",
            "priority": "mandatory",
            "acceptance_criteria": ["Invalid QR scans do not change attendance state."],
        },
        {
            "requirement_id": "R-004",
            "source_excerpt": "private admin attendance view",
            "normalized_requirement": "Provide an authenticated private admin view of attendance records.",
            "category": "interface",
            "priority": "mandatory",
            "acceptance_criteria": ["Unauthorized users cannot read private attendance records."],
        },
    ],
    "questions_requiring_user_input": [],
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


class CompilerClient:
    provider = "fake"
    model_id = "fake-compiler"

    def __init__(self, reviews=None, malformed_reviews=0):
        self.calls = []
        self.reviews = list(reviews or [APPROVED_REVIEW])
        self.malformed_reviews = malformed_reviews

    async def generate_structured(self, *, role, context, output_schema, request_id, timeout_seconds, attempt=1):
        schema_name = output_schema.__name__
        self.calls.append((role, schema_name))
        if role == "requirements_analyst":
            payload = ATTENDANCE_REQUIREMENTS
        elif role == "global_validator":
            if self.malformed_reviews:
                self.malformed_reviews -= 1
                return StructuredModelResult(
                    provider=self.provider,
                    model_id=self.model_id,
                    request_id=request_id,
                    attempt=attempt,
                    latency_ms=1,
                    input_tokens=25,
                    output_tokens=5,
                    structured_output=None,
                    raw_response="{not-json",
                    validation_errors=["invalid structured JSON"],
                    failure_classification=FailureClass.MALFORMED_OUTPUT,
                    context_digest=context.digest(),
                )
            payload = self.reviews.pop(0)
        elif role == "planner":
            raise AssertionError("normal compiler path must not spend a graph-planner call")
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
    orchestrator = CoverageAwareGraphPlanningOrchestrator(client)

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


def test_compiler_guided_attendance_plan_skips_initial_graph_model_call():
    client = CompilerClient()
    orchestrator = RecursiveRepairPlanningOrchestrator(client)

    outcome = asyncio.run(orchestrator.run("Build employee QR attendance."))

    assert client.calls == [
        ("requirements_analyst", "RequirementsAnalysis"),
        ("global_validator", "GraphReview"),
    ]
    assert outcome["planning_engine"] == "compiled_capability_graph_v2"
    assert outcome["expected_normal_model_calls"] == 2
    assert orchestrator.budget.calls == 2
    nodes = {node.node_id: node for node in outcome["nodes"]}
    assert "requirement_r_001" in nodes
    assert "requirement_r_002" in nodes
    assert "requirement_r_003" in nodes
    assert "requirement_r_004" in nodes
    assert "operly_interaction_verification" in nodes
    assert "operly_subject_identity" in nodes
    assert "operly_state_transition" in nodes
    assert "operly_durable_state" in nodes
    assert "operly_access_boundary" in nodes


def test_compiler_resolves_review_gap_before_spending_graph_repair_call():
    failed_review = {
        "approved": False,
        "missing_requirement_ids": [],
        "dependency_issues": [],
        "semantic_gaps": ["QR scan validation must explicitly reject invalid scans."],
        "unnecessary_implementation_details": [],
        "user_questions": [],
        "reasoning_summary": "QR rejection needs an explicit owner.",
    }
    client = CompilerClient(reviews=[failed_review, APPROVED_REVIEW])
    orchestrator = RecursiveRepairPlanningOrchestrator(client)

    outcome = asyncio.run(orchestrator.run("Build employee QR attendance."))

    assert all(role != "planner" for role, _ in client.calls)
    assert client.calls == [
        ("requirements_analyst", "RequirementsAnalysis"),
        ("global_validator", "GraphReview"),
        ("global_validator", "GraphReview"),
    ]
    assert outcome["compiler_repair_rounds"] == 1
    assert outcome["model_graph_repair_rounds"] == 0


def test_compiler_review_fallback_prevents_malformed_reviewer_json_from_killing_plan():
    client = CompilerClient(malformed_reviews=2)
    orchestrator = RecursiveRepairPlanningOrchestrator(client)

    outcome = asyncio.run(orchestrator.run("Build employee QR attendance."))

    assert outcome["global"].approved is True
    assert outcome["compiler_review_fallbacks"] == 1
    assert all(role != "planner" for role, _ in client.calls)
    assert client.calls == [
        ("requirements_analyst", "RequirementsAnalysis"),
        ("global_validator", "GraphReview"),
        ("global_validator", "GraphReview"),
    ]
