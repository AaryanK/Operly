"""Targeted requirement-to-node coverage repair for the dynamic capability graph.

Coverage defects should not cause the model to regenerate an otherwise coherent
architecture. The graph remains authoritative; this module asks for only the
missing requirement links, validates them deterministically, and merges them into
existing nodes.
"""
from __future__ import annotations

from typing import Any

from packages.custom_software.graph_planning import (
    CapabilityGraph,
    GraphPlanningOrchestrator,
    GraphReview,
    _approved_node_validation,
    _compact_graph,
    _compact_requirements,
    _graph_errors,
    _review_to_global,
    _to_proposed_nodes,
    _unique,
)
from packages.custom_software.live_planning import Contract, PlanningBlocked, PlanningContextPacket, RequirementsAnalysis


class CoverageAssignment(Contract):
    requirement_id: str
    node_ids: list[str]


class CoveragePatch(Contract):
    assignments: list[CoverageAssignment]


def missing_mandatory_requirement_ids(graph: CapabilityGraph, analysis: RequirementsAnalysis) -> list[str]:
    mandatory = {
        item.requirement_id
        for item in analysis.requirements
        if item.priority.lower() != "optional"
    }
    covered = {
        requirement_id
        for node in graph.nodes
        for requirement_id in node.requirement_ids
    }
    return sorted(mandatory - covered)


def _missing_requirement_payload(analysis: RequirementsAnalysis, missing: list[str]) -> list[dict[str, Any]]:
    wanted = set(missing)
    return [
        {
            "id": item.requirement_id,
            "requirement": item.normalized_requirement,
            "acceptance": list(item.acceptance_criteria),
            "category": item.category,
            "exclusions": list(item.exclusions),
        }
        for item in analysis.requirements
        if item.requirement_id in wanted
    ]


def apply_coverage_patch(
    graph: CapabilityGraph,
    patch: CoveragePatch,
    missing: list[str],
) -> CapabilityGraph:
    missing_set = set(missing)
    node_ids = {node.node_id for node in graph.nodes}
    assignments: dict[str, set[str]] = {requirement_id: set() for requirement_id in missing}

    for item in patch.assignments:
        requirement_id = str(item.requirement_id or "").strip()
        if requirement_id not in missing_set:
            raise PlanningBlocked(f"coverage patch modified non-missing requirement: {requirement_id}")
        targets = {str(node_id).strip() for node_id in item.node_ids if str(node_id).strip()}
        unknown = targets - node_ids
        if unknown:
            raise PlanningBlocked(
                f"coverage patch referenced unknown graph nodes for {requirement_id}: "
                + ", ".join(sorted(unknown))
            )
        if not targets:
            raise PlanningBlocked(f"coverage patch supplied no graph node for {requirement_id}")
        assignments[requirement_id].update(targets)

    still_missing = [requirement_id for requirement_id, targets in assignments.items() if not targets]
    if still_missing:
        raise PlanningBlocked(
            "coverage patch omitted mandatory requirements: " + ", ".join(sorted(still_missing))
        )

    nodes = []
    for node in graph.nodes:
        additions = [
            requirement_id
            for requirement_id in missing
            if node.node_id in assignments[requirement_id]
            and requirement_id not in node.requirement_ids
        ]
        nodes.append(
            node.model_copy(
                update={"requirement_ids": [*node.requirement_ids, *additions]}
            )
        )
    return CapabilityGraph(nodes=nodes)


def preserve_coverage_links(previous: CapabilityGraph, repaired: CapabilityGraph) -> CapabilityGraph:
    """Keep already validated requirement links when a later graph repair preserves node IDs."""
    previous_links = {node.node_id: list(node.requirement_ids) for node in previous.nodes}
    nodes = []
    for node in repaired.nodes:
        preserved = previous_links.get(node.node_id, [])
        merged = list(dict.fromkeys([*node.requirement_ids, *preserved]))
        nodes.append(node.model_copy(update={"requirement_ids": merged}))
    return CapabilityGraph(nodes=nodes)


class CoverageAwareGraphPlanningOrchestrator(GraphPlanningOrchestrator):
    """Graph planner that repairs missing coverage without regenerating architecture."""

    async def _repair_coverage(
        self,
        analysis: RequirementsAnalysis,
        graph: CapabilityGraph,
        missing: list[str],
    ) -> CapabilityGraph:
        context = PlanningContextPacket(
            role="planner",
            untrusted_requirements={
                "missing_requirements": _missing_requirement_payload(analysis, missing),
            },
            current_contract={"existing_nodes": _compact_graph(graph)},
            constraints={
                "operation": "attach_missing_requirement_ids_only",
                "missing_requirement_ids": missing,
                "existing_node_ids": [node.node_id for node in graph.nodes],
                "do_not_create_delete_or_rewrite_nodes": True,
                "do_not_change_dependencies_or_contract_fields": True,
                "assign_each_missing_requirement_to_one_or_more_existing_nodes": True,
                "choose_nodes_by_semantic_responsibility_not_keyword_overlap": True,
                "constraint_requirements_may_attach_to_multiple_relevant_nodes": True,
                "return_only_assignments": True,
            },
            budget={
                "remaining_calls": self.budget.max_model_calls - self.budget.calls,
                "remaining_tokens": self.budget.max_tokens - self.budget.tokens,
            },
        )
        patch = await self._call("planner", context, CoveragePatch, "coverage")
        return apply_coverage_patch(graph, patch, missing)

    async def run(self, prompt: str) -> dict[str, Any]:
        analysis = await self._requirements(prompt)
        graph = await self._plan_graph(analysis)

        missing = missing_mandatory_requirement_ids(graph, analysis)
        coverage_repairs = 0
        if missing:
            coverage_repairs = 1
            graph = await self._repair_coverage(analysis, graph, missing)

        deterministic = _graph_errors(graph, analysis)
        review = await self._review(analysis, graph, deterministic)

        repair_rounds = 0
        if deterministic or not review.approved:
            repair_rounds = 1
            prior_graph = graph
            graph = await self._plan_graph(
                analysis,
                current=graph,
                findings=self._review_findings(review, deterministic),
            )
            graph = preserve_coverage_links(prior_graph, graph)
            deterministic = _graph_errors(graph, analysis)
            review = await self._review(analysis, graph, deterministic)

        if deterministic:
            raise PlanningBlocked(
                "capability graph failed deterministic validation: " + "; ".join(deterministic[:20])
            )
        if not review.approved:
            findings = _unique(
                [
                    *review.missing_requirement_ids,
                    *review.dependency_issues,
                    *review.semantic_gaps,
                    *review.unnecessary_implementation_details,
                ]
            )
            raise PlanningBlocked(
                "capability graph failed semantic review: "
                + ("; ".join(findings[:20]) if findings else review.reasoning_summary)
            )

        nodes = _to_proposed_nodes(graph, analysis)
        validations = {node.node_id: _approved_node_validation() for node in nodes}
        return {
            "analysis": analysis,
            "nodes": nodes,
            "validations": validations,
            "global": _review_to_global(review),
            "budget": self.budget,
            "global_repair_rounds": repair_rounds,
            "dependency_resolution_traces": [],
            "planning_engine": "dynamic_capability_graph_v1",
            "expected_normal_model_calls": 3,
            "graph_repair_rounds": repair_rounds,
            "coverage_repair_rounds": coverage_repairs,
        }
