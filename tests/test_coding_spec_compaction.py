import json

from packages.software_projects.coding.source_service import _plan_specification, _repair_specification


class FakePlan:
    def model_dump(self, mode="json"):
        return {
            "projectName": "Arbitrary capability",
            "summary": "Build an arbitrary capability.",
            "effectiveRequirements": ["duplicate presentation requirement"],
            "requirementLedger": [
                {
                    "id": "R-001",
                    "exactText": "Let me create things.",
                    "normalizedMeaning": "Allow creation of things.",
                    "mandatory": True,
                    "acceptanceCriteria": ["A thing can be created."],
                    "relatedPlanNodeIds": ["CAPABILITY"],
                    "relatedArtifactIds": ["duplicate-artifact-presentation"],
                    "relatedTestIds": ["duplicate-test-presentation"],
                    "coverageStatus": "implementation_ready",
                    "verificationStatus": "unverified",
                    "planningMode": "live_llm",
                },
                {
                    "id": "R-002",
                    "exactText": "Persist created things.",
                    "normalizedMeaning": "Created things persist across reloads.",
                    "mandatory": True,
                    "acceptanceCriteria": ["Reload preserves a created thing."],
                    "relatedPlanNodeIds": ["PERSISTENCE"],
                },
                {
                    "id": "R-003",
                    "exactText": "Optional analytics.",
                    "normalizedMeaning": "Show optional analytics.",
                    "mandatory": False,
                    "acceptanceCriteria": [],
                    "relatedPlanNodeIds": ["ANALYTICS"],
                },
            ],
            "planTree": [
                {
                    "id": "CAPABILITY",
                    "title": "Thing capability",
                    "objective": "Create things.",
                    "responsibilities": ["Create one requested thing."],
                    "originalRequirementIds": ["R-001"],
                    "dependencies": [],
                    "inputs": ["creation request"],
                    "outputs": ["created thing"],
                    "invariants": ["Created things have stable identity."],
                    "failureCases": ["Invalid creation requests are rejected."],
                    "securityRequirements": [],
                    "persistenceBehavior": [],
                    "requiredTests": ["A thing can be created."],
                    "validation": {"large": "presentation-only validation prose"},
                    "provenance": {"large": "presentation-only provenance"},
                },
                {
                    "id": "PERSISTENCE",
                    "title": "Persistence",
                    "objective": "Persist things.",
                    "responsibilities": ["Persist created things."],
                    "originalRequirementIds": ["R-002"],
                    "dependencies": ["CAPABILITY"],
                    "inputs": ["created thing"],
                    "outputs": ["stored thing"],
                    "invariants": [],
                    "failureCases": [],
                    "securityRequirements": [],
                    "persistenceBehavior": ["Survives reload."],
                    "requiredTests": ["Reload preserves a created thing."],
                },
                {
                    "id": "ANALYTICS",
                    "title": "Analytics",
                    "objective": "Optional analytics.",
                    "responsibilities": [],
                    "originalRequirementIds": ["R-003"],
                    "dependencies": [],
                    "inputs": [],
                    "outputs": [],
                    "invariants": [],
                    "failureCases": [],
                    "securityRequirements": [],
                    "persistenceBehavior": [],
                    "requiredTests": [],
                },
            ],
            "globalValidation": {"passed": True},
            "unsupportedRequirements": [],
        }


def test_coding_handoff_removes_duplicate_planner_presentations():
    specification = _plan_specification(FakePlan())
    data = json.loads(specification)

    assert "effectiveRequirements" not in data
    assert "requirementLedger" not in data
    assert "planTree" not in data
    assert data["requirements"][0] == {
        "id": "R-001",
        "requirement": "Allow creation of things.",
        "mandatory": True,
        "acceptance": ["A thing can be created."],
        "nodeIds": ["CAPABILITY"],
        "source": "Let me create things.",
    }
    assert data["capabilityGraph"][0]["id"] == "CAPABILITY"
    assert "validation" not in data["capabilityGraph"][0]
    assert "provenance" not in data["capabilityGraph"][0]
    assert "duplicate presentation requirement" not in specification
    assert "presentation-only validation prose" not in specification


def test_repair_handoff_selects_requirement_and_node_named_by_runner_evidence():
    specification = _repair_specification(
        FakePlan(),
        {
            "classification": "test_failure",
            "message": "Acceptance for R-002 failed in PERSISTENCE after reload",
        },
    )
    data = json.loads(specification)

    assert [item["id"] for item in data["requirements"]] == ["R-002"]
    assert [item["id"] for item in data["capabilityGraph"]] == ["PERSISTENCE"]
    assert data["repairContext"]["referencedRequirementIds"] == ["R-002"]
    assert data["repairContext"]["referencedPlanNodeIds"] == ["PERSISTENCE"]
    assert data["repairContext"]["machineContractsIncluded"] is False
    assert "machineContracts" not in data["operlyExecutionContract"]
    assert "R-001" not in specification
    assert "R-003" not in specification


def test_repair_handoff_preserves_mandatory_behavior_when_evidence_has_no_requirement_id():
    specification = _repair_specification(
        FakePlan(),
        {
            "classification": "build_failure",
            "message": "backend/app.py failed to start on the declared health path",
        },
    )
    data = json.loads(specification)

    assert [item["id"] for item in data["requirements"]] == ["R-001", "R-002"]
    assert "R-003" not in specification
    assert data["repairContext"]["referencedRequirementIds"] == []
    assert data["repairContext"]["machineContractsIncluded"] is False
    assert "fullStack" in data["operlyExecutionContract"]
    assert "machineContracts" not in data["operlyExecutionContract"]
