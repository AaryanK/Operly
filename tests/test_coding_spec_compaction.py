import json

from packages.coding_harness.source_service import _plan_specification


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
                }
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
                }
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
    assert data["requirements"] == [
        {
            "id": "R-001",
            "requirement": "Allow creation of things.",
            "mandatory": True,
            "acceptance": ["A thing can be created."],
            "nodeIds": ["CAPABILITY"],
            "source": "Let me create things.",
        }
    ]
    assert data["capabilityGraph"][0]["id"] == "CAPABILITY"
    assert "validation" not in data["capabilityGraph"][0]
    assert "provenance" not in data["capabilityGraph"][0]
    assert "duplicate presentation requirement" not in specification
    assert "presentation-only validation prose" not in specification
