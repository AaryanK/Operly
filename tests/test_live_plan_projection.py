import json
from types import SimpleNamespace

from packages.software_projects.coding.source_service import _plan_specification
from packages.software_projects.planning.live_projection import project_live_envelope


def test_live_projection_discards_legacy_semantic_defaults():
    base = {
        "targetUsers": ["Administrator", "Authenticated User"],
        "roles": [{"id": "administrator"}],
        "entities": [{"id": "domain_record_1"}],
        "relationships": [{"id": "legacy_relationship"}],
        "workflows": [{"id": "legacy_workflow"}],
        "surfaces": [{"id": "legacy_surface"}],
        "stack": {"frontend": "model-selected", "backend": "model-selected", "database": "model-selected"},
        "securityConstraints": ["legacy default"],
        "capabilities": [],
        "architectureNodes": [],
        "backendCapabilities": [],
        "requirementEvidence": [],
        "generatedComponents": [],
        "reusedPrimitives": [],
        "testRequirements": [],
        "rationale": "legacy",
    }
    analysis = SimpleNamespace(
        requirements=[
            SimpleNamespace(normalized_requirement="Use HTML/CSS/vanilla JavaScript frontend"),
            SimpleNamespace(normalized_requirement="Use a Python standard-library web server"),
            SimpleNamespace(normalized_requirement="No authentication or database"),
        ]
    )
    node = SimpleNamespace(
        node_type="component",
        title="Calculator",
        objective="Implement the calculator",
        required_tests=["test_calculator"],
        security_constraints=["reject invalid input"],
        inputs=["numbers"],
        outputs=["result"],
        invariants=["division by zero returns a clear error"],
    )
    ledger = [
        {
            "id": "R-001",
            "normalizedMeaning": "No authentication or database",
            "relatedArtifactIds": ["calculator"],
            "relatedTestIds": ["test_calculator"],
        }
    ]

    projected = project_live_envelope(base, analysis, [node], ledger)

    assert projected["targetUsers"] == []
    assert projected["roles"] == []
    assert projected["entities"] == []
    assert projected["relationships"] == []
    assert projected["workflows"] == []
    assert projected["surfaces"] == []
    assert projected["stack"] is None
    assert projected["effectiveRequirements"][-1] == "No authentication or database"
    assert projected["testRequirements"] == ["test_calculator"]


def test_coding_harness_spec_excludes_legacy_presentation_and_duplicate_requirement_fields():
    plan = {
        "projectName": "Calculator",
        "summary": "Build a calculator",
        "effectiveRequirements": ["No authentication or database"],
        "requirementLedger": [{"id": "R-001", "normalizedMeaning": "No authentication or database"}],
        "planTree": [{"id": "ROOT"}],
        "globalValidation": {"passed": True},
        "unsupportedRequirements": [],
        "roles": [{"id": "administrator"}],
        "entities": [{"id": "domain_record_1"}],
        "stack": {"database": "model-selected"},
    }

    spec = json.loads(_plan_specification(plan))

    assert "roles" not in spec
    assert "entities" not in spec
    assert "stack" not in spec
    assert "effectiveRequirements" not in spec
    assert spec["requirements"][0]["id"] == "R-001"
    assert spec["requirements"][0]["requirement"] == "No authentication or database"
