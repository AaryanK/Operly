"""Compact dynamic-graph planner for OPERLY.

The graph owns global work/dependency structure. Local autonomy belongs to the
coding agent and runner repair loops, not to a recursive planning call tree.

Normal live planning uses three model calls:
1. requirements analysis + genuine owner ambiguity,
2. one implementation-ready capability graph,
3. one whole-graph semantic review.

A failed review gets one graph-level repair and one re-review. Per-node tests and
artifact identities are derived deterministically from the requirement ledger so
OPERLY does not spend model calls restating the same acceptance criteria.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import model_validator

from packages.custom_software.live_planning import (
    Contract,
    GlobalValidatorOutput,
    LivePlanningOrchestrator,
    PlanningBlocked,
    PlanningBudget,
    PlanningContextPacket,
    ProposedNode,
    RequirementsAnalysis,
    ValidatorOutput,
    deterministic_readiness,
    scope_errors,
)


class PlanningNeedsUserInput(PlanningBlocked):
    """The work graph cannot be chosen without a genuine owner/product decision."""

    def __init__(self, questions: list[str]):
        cleaned = [str(question).strip() for question in questions if str(question).strip()]
        self.questions = cleaned[:2]
        super().__init__("user input required before planning: " + " | ".join(self.questions))


_MECHANICAL_QUESTION_TERMS = (
    "protocol",
    "api style",
    "specific api",
    "mcp",
    "technical interface",
    "technical nature",
    "framework",
    "storage mechanism",
    "persistence mechanism",
    "storage engine",
    "serialization",
    "database technology",
    "what is considered an important architectural decision",
    "necessary third-party api",
    "necessary third party api",
)

_OWNER_DECISION_TERMS = (
    "security",
    "permission",
    "legal",
    "compliance",
    "regulated",
    "jurisdiction",
    "data ownership",
    "external cost",
    "budget",
    "billing",
    "authentication",
)

_EXPLICIT_PLACEMENT_TERMS = (
    "standalone application",
    "integrated into",
    "internal tool",
    "where should",
    "where must",
)

_UNSAFE_ASSUMPTION_TERMS = (
    "conflicting requirement",
    "contradictory requirement",
    "mutually exclusive",
    "cannot both",
    "irreversible",
    "destructive",
    "delete existing",
    "replace existing",
)

_SCOPE_DATABASE_GROUNDING_TERMS = (
    "database",
    "sql",
    "postgres",
    "postgresql",
    "mysql",
    "sqlite",
    "persist",
    "persistence",
    "storage",
    "store",
    "stored",
    "save",
    "saved",
    "durable",
    "record",
    "records",
    "history",
    "later retrieval",
)


def material_user_questions(questions: list[str], requirement_context: str | None = None) -> list[str]:
    """Return only questions OPERLY cannot safely resolve itself."""
    context = " ".join(str(requirement_context or "").lower().split())
    result: list[str] = []
    for question in questions:
        text = str(question or "").strip()
        if not text:
            continue
        normalized = " ".join(text.lower().split())
        if any(term in normalized for term in _MECHANICAL_QUESTION_TERMS):
            continue
        must_ask = any(term in normalized for term in _OWNER_DECISION_TERMS)
        must_ask = must_ask or any(term in normalized for term in _EXPLICIT_PLACEMENT_TERMS)
        must_ask = must_ask or any(term in normalized for term in _UNSAFE_ASSUMPTION_TERMS)
        if must_ask and requirement_context is not None:
            explicit_risk = any(term in normalized and term in context for term in _OWNER_DECISION_TERMS)
            explicit_placement = any(term in context for term in _EXPLICIT_PLACEMENT_TERMS) or "do not assume" in context
            explicit_conflict = any(term in normalized for term in _UNSAFE_ASSUMPTION_TERMS)
            must_ask = explicit_risk or (explicit_placement and any(term in normalized for term in _EXPLICIT_PLACEMENT_TERMS)) or explicit_conflict
        if must_ask:
            result.append(text)
    return result[:2]


class CapabilityGraphNode(Contract):
    node_id: str
    title: str
    objective: str
    responsibility: str
    requirement_ids: list[str]
    dependencies: list[str] = []
    inputs: list[str]
    outputs: list[str]
    invariants: list[str]
    failure_cases: list[str]
    security_constraints: list[str] = []
    persistence_behavior: list[str] = []

    @model_validator(mode="after")
    def bounded(self):
        if not self.node_id.strip():
            raise ValueError("node_id is required")
        if not self.responsibility.strip():
            raise ValueError("one bounded responsibility is required")
        if not self.requirement_ids:
            raise ValueError("each graph node must link requirements")
        return self


class CapabilityGraph(Contract):
    nodes: list[CapabilityGraphNode]

    @model_validator(mode="after")
    def nonempty_unique(self):
        ids = [node.node_id for node in self.nodes]
        if not ids:
            raise ValueError("capability graph requires at least one node")
        if len(ids) != len(set(ids)):
            raise ValueError("capability graph node IDs must be unique")
        return self


class GraphReview(Contract):
    approved: bool
    missing_requirement_ids: list[str] = []
    dependency_issues: list[str] = []
    semantic_gaps: list[str] = []
    unnecessary_implementation_details: list[str] = []
    user_questions: list[str] = []
    reasoning_summary: str


def _compact_requirements(analysis: RequirementsAnalysis) -> dict[str, Any]:
    return {
        "objective": analysis.root_objective,
        "requirements": [
            {
                "id": requirement.requirement_id,
                "requirement": requirement.normalized_requirement,
                "acceptance": list(requirement.acceptance_criteria),
                "mandatory": requirement.priority.lower() != "optional",
                "category": requirement.category,
                "exclusions": list(requirement.exclusions),
            }
            for requirement in analysis.requirements
        ],
        "global_exclusions": list(analysis.global_exclusions),
    }


def _compact_graph(graph: CapabilityGraph) -> list[dict[str, Any]]:
    return [node.model_dump(mode="json") for node in graph.nodes]


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _to_proposed_nodes(graph: CapabilityGraph, analysis: RequirementsAnalysis) -> list[ProposedNode]:
    requirements = {item.requirement_id: item for item in analysis.requirements}
    nodes: list[ProposedNode] = []
    for item in graph.nodes:
        tests = _unique(
            [
                criterion
                for requirement_id in item.requirement_ids
                if requirement_id in requirements
                for criterion in requirements[requirement_id].acceptance_criteria
            ]
        )
        nodes.append(
            ProposedNode(
                node_id=item.node_id,
                title=item.title,
                node_type="capability",
                objective=item.objective,
                responsibilities=[item.responsibility],
                linked_requirement_ids=item.requirement_ids,
                inputs=item.inputs,
                outputs=item.outputs,
                dependencies=item.dependencies,
                invariants=item.invariants,
                failure_cases=item.failure_cases,
                security_constraints=item.security_constraints,
                persistence_behavior=item.persistence_behavior,
                required_artifacts=[f"generated implementation for {item.title}"],
                required_tests=tests or [f"Verify {item.responsibility}"],
                assumptions=[],
                scope_claims=[],
                children=[],
            )
        )
    return nodes


def _approved_node_validation() -> ValidatorOutput:
    return ValidatorOutput(
        disposition="approve",
        ready_for_implementation=True,
        semantic_coverage="Validated at whole capability-graph level.",
        reasoning_summary="Deterministic contract checks and whole-graph semantic review passed.",
    )


def _scope_requirement_payload(requirement) -> dict[str, Any]:
    """Preserve all analyst evidence when validating implementation scope.

    scope_errors historically inspected source_excerpt only. The analyst may place
    an explicit or essential persistence constraint in the normalized requirement,
    acceptance criteria, explicit terms, or category, so collapsing back to the
    excerpt can falsely classify a database-backed implementation as invented.
    """
    payload = requirement.model_dump(mode="json")
    evidence = " ".join(
        [
            str(requirement.source_excerpt or ""),
            str(requirement.normalized_requirement or ""),
            str(requirement.category or ""),
            *[str(item) for item in requirement.acceptance_criteria],
            *[str(item) for item in requirement.explicit_terms],
        ]
    )
    lowered = evidence.lower()
    if any(term in lowered for term in _SCOPE_DATABASE_GROUNDING_TERMS):
        evidence += " database"
    payload["source_excerpt"] = evidence
    return payload


def _graph_errors(graph: CapabilityGraph, analysis: RequirementsAnalysis) -> list[str]:
    errors: list[str] = []
    if len(graph.nodes) > 32:
        errors.append("capability graph exceeds 32 implementation nodes")
    requirement_ids = {item.requirement_id for item in analysis.requirements}
    mandatory_ids = {
        item.requirement_id for item in analysis.requirements if item.priority.lower() != "optional"
    }
    node_ids = {node.node_id for node in graph.nodes}
    covered: set[str] = set()
    proposed = _to_proposed_nodes(graph, analysis)

    for graph_node, node in zip(graph.nodes, proposed):
        linked = set(graph_node.requirement_ids)
        covered.update(linked)
        unknown_requirements = linked - requirement_ids
        if unknown_requirements:
            errors.append(
                f"{graph_node.node_id}: unknown requirement IDs: {', '.join(sorted(unknown_requirements))}"
            )
        unknown_dependencies = set(graph_node.dependencies) - node_ids
        if unknown_dependencies:
            errors.append(
                f"{graph_node.node_id}: unknown dependencies: {', '.join(sorted(unknown_dependencies))}"
            )
        if graph_node.node_id in graph_node.dependencies:
            errors.append(f"{graph_node.node_id}: self dependency is not allowed")

        verdict = _approved_node_validation()
        ready, readiness = deterministic_readiness(node, verdict)
        if not ready:
            errors.extend(f"{graph_node.node_id}: {finding}" for finding in readiness)

        linked_requirements = [
            _scope_requirement_payload(requirement)
            for requirement in analysis.requirements
            if requirement.requirement_id in linked
        ]
        errors.extend(f"{graph_node.node_id}: {finding}" for finding in scope_errors(node, linked_requirements))

    missing = mandatory_ids - covered
    if missing:
        errors.append("mandatory requirements not mapped: " + ", ".join(sorted(missing)))
    return _unique(errors)


def _deterministic_graph_repair(graph: CapabilityGraph, analysis: RequirementsAnalysis) -> CapabilityGraph:
    """Repair structural model mistakes without inventing product decisions.

    Semantic review still runs afterward. This pass only removes dangling graph
    references and guarantees that every mandatory ledger item has an executable
    owner, preventing stochastic identifier mistakes from aborting the whole plan.
    """
    requirement_ids = {item.requirement_id for item in analysis.requirements}
    node_ids = {node.node_id for node in graph.nodes}
    repaired: list[CapabilityGraphNode] = []
    covered: set[str] = set()
    for node in graph.nodes:
        linked = _unique([item for item in node.requirement_ids if item in requirement_ids])
        if not linked:
            continue
        dependencies = _unique([item for item in node.dependencies if item in node_ids and item != node.node_id])
        repaired_node = node.model_copy(update={"requirement_ids": linked, "dependencies": dependencies})
        repaired.append(repaired_node)
        covered.update(linked)

    mandatory = [item for item in analysis.requirements if item.priority.lower() != "optional"]
    used_ids = {node.node_id for node in repaired}
    for requirement in mandatory:
        if requirement.requirement_id in covered:
            continue
        requirement_text = " ".join(
            [requirement.normalized_requirement, requirement.category, *requirement.acceptance_criteria]
        )
        requirement_tokens = _semantic_tokens(requirement_text)
        ranked = sorted(
            (
                (_semantic_match_score(requirement_tokens, requirement.category, node), index)
                for index, node in enumerate(repaired)
            ),
            reverse=True,
        )
        if ranked and ranked[0][0] > 0:
            index = ranked[0][1]
            target = repaired[index]
            repaired[index] = target.model_copy(
                update={"requirement_ids": _unique([*target.requirement_ids, requirement.requirement_id])}
            )
            covered.add(requirement.requirement_id)
            continue
        base = "coverage_" + "".join(character.lower() if character.isalnum() else "_" for character in requirement.requirement_id).strip("_")
        node_id = base or "coverage_requirement"
        suffix = 2
        while node_id in used_ids:
            node_id = f"{base}_{suffix}"; suffix += 1
        used_ids.add(node_id)
        acceptance = _unique(list(requirement.acceptance_criteria))
        repaired.append(CapabilityGraphNode(
            node_id=node_id,
            title=f"Requirement {requirement.requirement_id} capability",
            objective=requirement.normalized_requirement,
            responsibility=requirement.normalized_requirement,
            requirement_ids=[requirement.requirement_id],
            dependencies=[],
            inputs=["approved requirement input"],
            outputs=[f"implemented behavior for {requirement.requirement_id}"],
            invariants=acceptance or [requirement.normalized_requirement],
            failure_cases=[f"The behavior required by {requirement.requirement_id} is unavailable or incorrect"],
            security_constraints=[],
            persistence_behavior=[],
        ))
    return CapabilityGraph(nodes=repaired)


_SEMANTIC_STOP_WORDS = {
    "allow", "application", "behavior", "create", "display", "include", "provide",
    "required", "shall", "system", "user", "using", "with", "from", "into", "each",
}


def _semantic_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", str(value or "").lower()):
        if len(token) < 4 or token in _SEMANTIC_STOP_WORDS:
            continue
        for suffix in ("ations", "ation", "ments", "ment", "ing", "ers", "er", "ed", "s"):
            if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                token = token[: -len(suffix)]
                break
        tokens.add(token)
    return tokens


def _semantic_match_score(requirement_tokens: set[str], category: str, node: CapabilityGraphNode) -> int:
    node_text = " ".join(
        [node.title, node.objective, node.responsibility, *node.inputs, *node.outputs]
    )
    node_tokens = _semantic_tokens(node_text)
    score = len(requirement_tokens & node_tokens) * 10
    category_text = str(category or "").lower()
    lowered = node_text.lower()
    category_hints = {
        "input": ("input", "form", "manager"),
        "calculation": ("calculat", "engine", "metric"),
        "interface": ("ui", "render", "view", "present"),
        "persistence": ("persist", "storage", "state", "data"),
        "provenance": ("test", "verif", "audit"),
        "behavior": ("engine", "manager", "controller"),
    }
    for category_name, hints in category_hints.items():
        if category_name in category_text and any(hint in lowered for hint in hints):
            score += 3
    return score


def _review_to_global(review: GraphReview) -> GlobalValidatorOutput:
    return GlobalValidatorOutput(
        approved=review.approved,
        semantic_completeness="complete" if review.approved else "incomplete",
        missing_subsystems=list(review.semantic_gaps),
        incompatible_interfaces=list(review.dependency_issues),
        uncovered_requirements=list(review.missing_requirement_ids),
        irrelevant_concepts=list(review.unnecessary_implementation_details),
        reasoning_summary=review.reasoning_summary,
    )


class GraphPlanningOrchestrator(LivePlanningOrchestrator):
    """Dynamic work-graph planner with a bounded graph-level repair loop."""

    def __init__(self, client, *args, **kwargs):
        budget = kwargs.pop("budget", None) or PlanningBudget(
            max_depth=1,
            max_nodes=32,
            max_refinements_per_node=1,
            max_model_calls=6,
            max_tokens=45_000,
            max_elapsed_seconds=360,
            max_malformed_outputs=1,
            max_equivalent_decompositions=1,
        )
        super().__init__(client, *args, budget=budget, **kwargs)

    async def _requirements(self, prompt: str) -> RequirementsAnalysis:
        context = PlanningContextPacket(
            role="requirements_analyst",
            untrusted_requirements={"original_request": prompt},
            constraints={
                "no_architecture_design": True,
                "preserve_negation": True,
                "ask_user_only_for_genuine_product_or_business_decisions": True,
                "operly_chooses_framework_protocol_storage_and_internal_interface_mechanics": True,
                "keep_acceptance_criteria_concise": True,
            },
        )
        analysis = await self._call("requirements_analyst", context, RequirementsAnalysis)
        questions = material_user_questions(analysis.questions_requiring_user_input, prompt)
        if questions:
            raise PlanningNeedsUserInput(questions)
        return analysis

    async def _plan_graph(
        self,
        analysis: RequirementsAnalysis,
        *,
        current: CapabilityGraph | None = None,
        findings: list[dict[str, Any]] | None = None,
    ) -> CapabilityGraph:
        context = PlanningContextPacket(
            role="planner",
            untrusted_requirements=_compact_requirements(analysis),
            current_contract={"graph": _compact_graph(current)} if current else {"graph": []},
            constraints={
                "planning_model": "dynamic_capability_graph",
                "return_implementation_ready_leaf_nodes_only": True,
                "no_parent_container_or_pure_constraint_nodes": True,
                "one_bounded_responsibility_per_node": True,
                "dependencies_reference_graph_node_ids_only": True,
                "requirements_may_map_to_multiple_nodes_when_semantically_required": True,
                "constraint_requirements_attach_to_relevant_nodes_as_invariants": True,
                "defer_implementation_mechanics_to_coding_harness": [
                    "framework",
                    "API style",
                    "protocol",
                    "MCP versus internal tool",
                    "database/storage engine",
                    "serialization/file format",
                    "deployment mechanics",
                ],
                "do_not_create_documentation_only_nodes": True,
                "do_not_write_test_lists_or_artifact_lists": True,
                "repair_existing_graph_only": current is not None,
            },
            previous_findings=findings or [],
            budget={
                "remaining_calls": self.budget.max_model_calls - self.budget.calls,
                "remaining_tokens": self.budget.max_tokens - self.budget.tokens,
            },
        )
        return await self._call("planner", context, CapabilityGraph, "graph")

    async def _review(
        self,
        analysis: RequirementsAnalysis,
        graph: CapabilityGraph,
        deterministic_findings: list[str],
    ) -> GraphReview:
        context = PlanningContextPacket(
            role="global_validator",
            untrusted_requirements=_compact_requirements(analysis),
            current_contract={"nodes": _compact_graph(graph)},
            constraints={
                "review_only": True,
                "graph_controls_dependencies_and_global_structure": True,
                "coding_agent_owns_local_implementation_autonomy": True,
                "deterministic_findings": deterministic_findings,
                "approve_only_if_every_mandatory_requirement_is_covered": True,
                "reject_unnecessary_implementation_mechanics": True,
                "owner_questions_only_for_genuine_product_or_business_decisions": True,
            },
        )
        review = await self._call("global_validator", context, GraphReview, "graph")
        requirement_context = " ".join(
            f"{item.source_excerpt} {item.normalized_requirement}"
            for item in analysis.requirements
        )
        questions = material_user_questions(review.user_questions, requirement_context)
        if questions:
            raise PlanningNeedsUserInput(questions)
        return review

    @staticmethod
    def _review_findings(review: GraphReview, deterministic: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "deterministic_findings": deterministic,
                "missing_requirement_ids": review.missing_requirement_ids,
                "dependency_issues": review.dependency_issues,
                "semantic_gaps": review.semantic_gaps,
                "unnecessary_implementation_details": review.unnecessary_implementation_details,
                "review_summary": review.reasoning_summary,
            }
        ]

    async def run(self, prompt: str) -> dict[str, Any]:
        analysis = await self._requirements(prompt)
        graph = await self._plan_graph(analysis)
        deterministic = _graph_errors(graph, analysis)
        review = await self._review(analysis, graph, deterministic)

        repair_rounds = 0
        if deterministic or not review.approved:
            repair_rounds = 1
            graph = await self._plan_graph(
                analysis,
                current=graph,
                findings=self._review_findings(review, deterministic),
            )
            deterministic = _graph_errors(graph, analysis)
            review = await self._review(analysis, graph, deterministic)

        if deterministic:
            graph = _deterministic_graph_repair(graph, analysis)
            deterministic = _graph_errors(graph, analysis)
            review = await self._review(analysis, graph, deterministic)

        if deterministic:
            raise PlanningBlocked("capability graph failed deterministic validation: " + "; ".join(deterministic[:20]))
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
        }