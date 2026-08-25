from types import SimpleNamespace

import packages.software_projects.planning.plan_service as plan_service


class _Global:
    approved = True

    def model_dump(self, mode="json"):
        return {
            "approved": True,
            "semantic_completeness": "complete",
            "missing_subsystems": [],
            "incompatible_interfaces": [],
            "missing_integrations": [],
            "missing_state_transitions": [],
            "uncovered_requirements": [],
            "superficial_tests": [],
            "irrelevant_concepts": [],
            "contradictions": [],
            "incomplete_user_journeys": [],
            "reasoning_summary": "complete",
        }


def test_live_inventory_projection_never_invokes_legacy_planner(monkeypatch):
    def legacy_must_not_run(_prompt):
        raise AssertionError("live projection invoked legacy planner")

    monkeypatch.setattr(plan_service, "build_software_plan", legacy_must_not_run)

    requirement = SimpleNamespace(
        requirement_id="R-001",
        source_excerpt="Inventory must still be there when I come back later.",
        normalized_requirement="Persist inventory across visits.",
        priority="mandatory",
        category="persistence",
        acceptance_criteria=["Saved inventory is restored on a later visit."],
        explicit_terms=[],
        exclusions=[],
        ambiguities=[],
        conflicts=[],
        assumptions=[],
    )
    node = SimpleNamespace(
        node_id="inventory_state",
        title="Inventory state",
        node_type="state component",
        objective="Persist and restore inventory state.",
        responsibilities=["Persist inventory mutations and restore inventory on a later visit."],
        linked_requirement_ids=["R-001"],
        inputs=["inventory mutations"],
        outputs=["restored inventory state"],
        dependencies=[],
        state_effects=["store inventory state"],
        invariants=["saved inventory can be restored"],
        failure_cases=["persistence write or read failure"],
        security_constraints=[],
        persistence_behavior=["persist inventory across visits"],
        required_artifacts=["inventory_state_component"],
        required_tests=["test_inventory_persists_across_visits"],
        assumptions=[],
    )
    verdict = SimpleNamespace(
        missing_information=[],
        ambiguous_behavior=[],
        missing_inputs=[],
        missing_outputs=[],
        missing_invariants=[],
        missing_dependencies=[],
        missing_failure_handling=[],
        missing_security_rules=[],
        missing_persistence_behavior=[],
        missing_tests=[],
        requirement_conflicts=[],
        recommended_decomposition=[],
    )
    budget = SimpleNamespace(
        max_depth=8,
        max_nodes=120,
        max_refinements_per_node=4,
        max_model_calls=80,
        max_tokens=200_000,
        max_elapsed_seconds=900,
        calls=1,
    )
    outcome = {
        "analysis": SimpleNamespace(root_objective="Build a simple inventory tracker.", requirements=[requirement]),
        "nodes": [node],
        "validations": {"inventory_state": verdict},
        "budget": budget,
        "global": _Global(),
        "invocations": [],
    }

    plan = plan_service._live_plan("Build a simple inventory tracker.", outcome)

    assert plan.planningMode == "live_llm"
    assert plan.planningMetrics.globalValidationPassed is True
    assert plan.planningMetrics.mandatoryRequirementsMapped == 1
    assert plan.targetUsers == []
    assert plan.roles == []
    assert plan.entities == []
    assert plan.stack is None
    assert plan.provenance["legacyPlannerInvokedForLiveProjection"] is False
    assert plan.requirementLedger[0].normalizedMeaning == "Persist inventory across visits."
