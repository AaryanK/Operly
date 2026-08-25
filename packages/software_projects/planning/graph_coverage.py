"""Targeted requirement-to-node coverage repair for the dynamic capability graph.

Coverage defects should not cause the model to regenerate an otherwise coherent
architecture. The graph remains authoritative; this module asks for only the
missing requirement links, validates them deterministically, and merges them into
existing nodes.

Semantic review is a stronger signal than syntactic requirement coverage. A graph
repair must therefore be allowed to remove a requirement link that a previous
coverage pass attached to the wrong node. Otherwise a clock-in endpoint can keep
claiming dashboard/QR/auth requirements forever simply because the IDs were once
present on that node.
"""
from __future__ import annotations

from typing import Any

from packages.software_projects.planning.graph_planning import (
    CapabilityGraph,
    CapabilityGraphNode,
    GraphPlanningOrchestrator,
    GraphReview,
    _approved_node_validation,
    _compact_graph,
    _compact_requirements,
    _deterministic_graph_repair,
    _graph_errors,
    _review_to_global,
    _semantic_match_score,
    _semantic_tokens,
    _to_proposed_nodes,
    _unique,
)
from packages.software_projects.planning.live_planning import Contract, PlanningBlocked, PlanningContextPacket, RequirementsAnalysis


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


def _requirement_score(requirement, node: CapabilityGraphNode) -> int:
    text = " ".join(
        [
            requirement.source_excerpt,
            requirement.normalized_requirement,
            requirement.category,
            *requirement.acceptance_criteria,
        ]
    )
    return _semantic_match_score(_semantic_tokens(text), requirement.category, node)


def _constraint_like(requirement) -> bool:
    text = " ".join(
        [requirement.category, requirement.normalized_requirement, requirement.source_excerpt]
    ).lower()
    return any(
        marker in text
        for marker in (
            "constraint",
            "security",
            "permission",
            "authorization",
            "authentication",
            "invariant",
            "compliance",
        )
    )


def normalize_semantic_requirement_ownership(
    graph: CapabilityGraph,
    analysis: RequirementsAnalysis,
) -> CapabilityGraph:
    """Drop stale duplicate requirement claims after semantic graph repair.

    Coverage links are evidence, not immutable state. For ordinary behavior/UI/data
    requirements that appear on multiple nodes, keep the strongest semantic owner
    and discard weaker stale copies. Constraint/security requirements are allowed to
    span multiple nodes because they often intentionally govern several surfaces.
    """
    requirements = {item.requirement_id: item for item in analysis.requirements}
    scores: dict[str, dict[str, int]] = {}
    for node in graph.nodes:
        for requirement_id in node.requirement_ids:
            requirement = requirements.get(requirement_id)
            if requirement is None:
                continue
            scores.setdefault(requirement_id, {})[node.node_id] = _requirement_score(requirement, node)

    nodes: list[CapabilityGraphNode] = []
    for node in graph.nodes:
        kept: list[str] = []
        for requirement_id in node.requirement_ids:
            requirement = requirements.get(requirement_id)
            if requirement is None:
                kept.append(requirement_id)
                continue
            candidates = scores.get(requirement_id, {})
            if len(candidates) <= 1 or _constraint_like(requirement):
                kept.append(requirement_id)
                continue
            best = max(candidates.values()) if candidates else 0
            current = candidates.get(node.node_id, 0)
            if current >= best:
                kept.append(requirement_id)
        if kept:
            nodes.append(node.model_copy(update={"requirement_ids": _unique(kept)}))
    return CapabilityGraph(nodes=nodes)


def _fallback_node(requirement, used_ids: set[str]) -> CapabilityGraphNode:
    base = "coverage_" + "".join(
        character.lower() if character.isalnum() else "_"
        for character in requirement.requirement_id
    ).strip("_")
    node_id = base or "coverage_requirement"
    suffix = 2
    while node_id in used_ids:
        node_id = f"{base}_{suffix}"
        suffix += 1
    used_ids.add(node_id)

    acceptance = _unique(list(requirement.acceptance_criteria))
    requirement_text = requirement.normalized_requirement.strip() or requirement.source_excerpt.strip()
    lower = " ".join([requirement.category, requirement_text]).lower()
    security = [requirement_text] if any(
        term in lower for term in ("auth", "permission", "security", "access", "admin")
    ) else []
    persistence = [requirement_text] if any(
        term in lower for term in ("persist", "store", "record", "history", "database", "attendance", "state")
    ) else []

    return CapabilityGraphNode(
        node_id=node_id,
        title=requirement_text[:120] or f"Requirement {requirement.requirement_id}",
        objective=requirement_text,
        responsibility=requirement_text,
        requirement_ids=[requirement.requirement_id],
        dependencies=[],
        inputs=[f"validated inputs and current state for {requirement.requirement_id}"],
        outputs=[acceptance[0] if acceptance else f"implemented outcome for {requirement.requirement_id}"],
        invariants=acceptance or [requirement_text],
        failure_cases=[f"Reject or surface a clear failure when {requirement_text} cannot be completed safely"],
        security_constraints=security,
        persistence_behavior=persistence,
    )


def restore_missing_requirements_as_leaf_nodes(
    graph: CapabilityGraph,
    analysis: RequirementsAnalysis,
) -> CapabilityGraph:
    """Guarantee semantic repair cannot lose mandatory work.

    This is intentionally used *after* a failed semantic review. At that point we
    prefer a small explicit requirement-owned leaf over reattaching the ID to an
    unrelated existing node merely to satisfy syntactic coverage.
    """
    missing = missing_mandatory_requirement_ids(graph, analysis)
    if not missing:
        return graph
    requirements = {item.requirement_id: item for item in analysis.requirements}
    used_ids = {node.node_id for node in graph.nodes}
    nodes = list(graph.nodes)
    for requirement_id in missing:
        requirement = requirements.get(requirement_id)
        if requirement is not None:
            nodes.append(_fallback_node(requirement, used_ids))
    return CapabilityGraph(nodes=nodes)


def semantic_claim_errors(graph: CapabilityGraph, analysis: RequirementsAnalysis) -> list[str]:
    """Expose obviously overloaded requirement claims to the semantic repair turn."""
    requirements = {item.requirement_id: item for item in analysis.requirements}
    findings: list[str] = []
    for node in graph.nodes:
        if len(node.requirement_ids) <= 1:
            continue
        for requirement_id in node.requirement_ids:
            requirement = requirements.get(requirement_id)
            if requirement is None or _constraint_like(requirement):
                continue
            if _requirement_score(requirement, node) <= 0:
                findings.append(
                    f"{node.node_id}: requirement {requirement_id} is linked for coverage but the node responsibility does not semantically implement it"
                )
    return _unique(findings)


def _semantic_repair_findings(
    review: GraphReview,
    deterministic: list[str],
    inherited: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        *inherited,
        {
            "repair_contract": {
                "operation": "replace_graph_to_resolve_semantic_review",
                "return_complete_replacement_graph": True,
                "resolve_every_review_finding": True,
                "may_add_split_replace_or_remove_nodes": True,
                "remove_requirement_ids_from_nodes_that_do_not_directly_implement_them": True,
                "do_not_preserve_old_requirement_links_only_for_coverage": True,
                "missing_requirement_ids_need_direct_executable_owners": list(review.missing_requirement_ids),
                "missing_subsystems_must_become_bounded_executable_leaf_nodes": list(review.semantic_gaps),
                "dependency_issues_must_be_resolved": list(review.dependency_issues),
                "unnecessary_implementation_details_must_be_removed": list(review.unnecessary_implementation_details),
                "deterministic_findings_must_be_resolved": list(deterministic),
            }
        },
    ]


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

        graph = _deterministic_graph_repair(graph, analysis)
        deterministic = _unique([*_graph_errors(graph, analysis), *semantic_claim_errors(graph, analysis)])
        review = await self._review(analysis, graph, deterministic)

        repair_rounds = 0
        if deterministic or not review.approved:
            repair_rounds = 1
            inherited_findings = self._review_findings(review, deterministic)
            graph = await self._plan_graph(
                analysis,
                current=graph,
                findings=_semantic_repair_findings(review, deterministic, inherited_findings),
            )
            # Semantic repair is authoritative. Do not re-add requirement IDs from
            # the rejected graph: those links may be the exact reason review failed.
            graph = normalize_semantic_requirement_ownership(graph, analysis)
            graph = restore_missing_requirements_as_leaf_nodes(graph, analysis)
            graph = _deterministic_graph_repair(graph, analysis)
            deterministic = _unique([*_graph_errors(graph, analysis), *semantic_claim_errors(graph, analysis)])
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
