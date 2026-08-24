"""Deterministic compiler layer for Studio capability planning.

The requirements analyst remains semantic: it extracts what the owner asked for.
OPERLY then turns that ledger into a conservative executable baseline graph and
adds platform obligations that are implied by the requested behavior. Model calls
are reserved for semantic review and bounded repair instead of being the only way
to invent the graph.

This deliberately avoids framework/storage/provider choices. Those remain coding-
harness/runtime concerns.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from packages.custom_software.graph_coverage import (
    CoverageAwareGraphPlanningOrchestrator,
    _semantic_repair_findings,
    missing_mandatory_requirement_ids,
    normalize_semantic_requirement_ownership,
    restore_missing_requirements_as_leaf_nodes,
    semantic_claim_errors,
)
from packages.custom_software.graph_planning import (
    CapabilityGraph,
    CapabilityGraphNode,
    GraphReview,
    PlanningNeedsUserInput,
    _approved_node_validation,
    _graph_errors,
    _review_to_global,
    _semantic_tokens,
    _to_proposed_nodes,
    _unique,
)
from packages.custom_software.live_planning import (
    PlanningBlocked,
    PlannerUnavailable,
    RequirementsAnalysis,
)

PLANNING_ENGINE_VERSION = "compiled_capability_graph_v2"

_ACTOR_TERMS = (
    "employee",
    "customer",
    "member",
    "student",
    "patient",
    "staff",
    "worker",
    "user",
    "person",
)
_MUTATION_TERMS = (
    "clock in",
    "clock out",
    "check in",
    "check out",
    "create",
    "record",
    "submit",
    "update",
    "approve",
    "reject",
    "start",
    "stop",
    "activate",
    "deactivate",
    "book",
    "cancel",
)
_SCAN_TERMS = ("qr", "barcode", "scan", "token")
_STATE_TERMS = (
    "state",
    "status",
    "transition",
    "clock in",
    "clock out",
    "check in",
    "check out",
    "start",
    "stop",
    "approve",
    "reject",
    "activate",
    "deactivate",
)
_PERSISTENCE_TERMS = (
    "persist",
    "save",
    "store",
    "record",
    "history",
    "timestamp",
    "later retrieval",
    "durable",
)
_ACCESS_TERMS = (
    "authenticate",
    "authentication",
    "authorize",
    "authorization",
    "permission",
    "private",
    "admin",
    "manager",
    "owner-only",
    "staff-only",
)


def _requirement_text(requirement) -> str:
    return " ".join(
        [
            str(requirement.category or ""),
            str(requirement.source_excerpt or ""),
            str(requirement.normalized_requirement or ""),
            *[str(item) for item in requirement.acceptance_criteria],
        ]
    ).lower()


def _slug(value: str, fallback: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return (clean[:60] or fallback).strip("_")


def _requirement_node(requirement) -> CapabilityGraphNode:
    text = requirement.normalized_requirement.strip() or requirement.source_excerpt.strip()
    lower = _requirement_text(requirement)
    acceptance = _unique(list(requirement.acceptance_criteria))
    category = str(requirement.category or "").lower()

    if "interface" in category or "input" in category:
        inputs = ["validated user interaction or input"]
    else:
        inputs = ["validated request and current application state"]

    security = [text] if any(term in lower for term in _ACCESS_TERMS) else []
    persistence = (
        [f"Preserve accepted state needed to satisfy {requirement.requirement_id}."]
        if any(term in lower for term in _PERSISTENCE_TERMS)
        else []
    )
    node_id = f"requirement_{_slug(requirement.requirement_id, 'requirement')}"

    return CapabilityGraphNode(
        node_id=node_id,
        title=text[:120] or f"Requirement {requirement.requirement_id}",
        objective=text,
        responsibility=text,
        requirement_ids=[requirement.requirement_id],
        dependencies=[],
        inputs=inputs,
        outputs=[
            acceptance[0]
            if acceptance
            else f"Observable outcome satisfying {requirement.requirement_id}"
        ],
        invariants=acceptance or [text],
        failure_cases=[
            f"Reject or surface a clear failure when {text} cannot be completed safely."
        ],
        security_constraints=security,
        persistence_behavior=persistence,
    )


def compile_requirement_graph(analysis: RequirementsAnalysis) -> CapabilityGraph:
    """Compile one bounded semantic owner per extracted requirement.

    The requirements analyst has already been instructed to split independently
    testable behaviors. A one-requirement leaf is therefore a safer baseline than
    asking another model to re-invent the whole graph before validation.
    """
    nodes = [_requirement_node(requirement) for requirement in analysis.requirements]
    return CapabilityGraph(nodes=nodes)


def _matching_requirement_ids(
    analysis: RequirementsAnalysis,
    terms: tuple[str, ...],
) -> list[str]:
    result = []
    for requirement in analysis.requirements:
        text = _requirement_text(requirement)
        if any(term in text for term in terms):
            result.append(requirement.requirement_id)
    return _unique(result)


def _actor_mutation_ids(analysis: RequirementsAnalysis) -> list[str]:
    result = []
    for requirement in analysis.requirements:
        text = _requirement_text(requirement)
        if any(actor in text for actor in _ACTOR_TERMS) and any(
            mutation in text for mutation in _MUTATION_TERMS
        ):
            result.append(requirement.requirement_id)
    return _unique(result)


def _platform_obligation_nodes(
    analysis: RequirementsAnalysis,
) -> list[CapabilityGraphNode]:
    scan_ids = _matching_requirement_ids(analysis, _SCAN_TERMS)
    state_ids = _matching_requirement_ids(analysis, _STATE_TERMS)
    actor_ids = _actor_mutation_ids(analysis)
    durable_ids = _unique(
        [
            *_matching_requirement_ids(analysis, _PERSISTENCE_TERMS),
            *state_ids,
        ]
    )
    access_ids = _matching_requirement_ids(analysis, _ACCESS_TERMS)

    nodes: list[CapabilityGraphNode] = []

    if scan_ids:
        nodes.append(
            CapabilityGraphNode(
                node_id="operly_interaction_verification",
                title="Interaction verification",
                objective="Verify scanned or tokenized interactions before they can mutate state.",
                responsibility="Validate each scanned code or token and map it to an allowed action before state mutation.",
                requirement_ids=scan_ids,
                dependencies=[],
                inputs=["untrusted scanned interaction payload"],
                outputs=["validated action intent"],
                invariants=[
                    "Unknown, expired, malformed, or mismatched scan values cannot trigger a state mutation."
                ],
                failure_cases=[
                    "Reject invalid scan values with an explicit non-mutating failure."
                ],
                security_constraints=["Treat every scanned value as untrusted input."],
                persistence_behavior=[],
            )
        )

    if actor_ids:
        nodes.append(
            CapabilityGraphNode(
                node_id="operly_subject_identity",
                title="Subject identity association",
                objective="Bind each state-changing action to the intended subject.",
                responsibility="Associate each state-changing request with the intended subject identity before recording it.",
                requirement_ids=actor_ids,
                dependencies=[],
                inputs=["validated action request", "subject identity context"],
                outputs=["action bound to one subject identity"],
                invariants=[
                    "A state-changing record must never be attributed to an ambiguous or different subject."
                ],
                failure_cases=[
                    "Reject a mutation when the intended subject cannot be established."
                ],
                security_constraints=[
                    "Do not infer a different subject when identity evidence is missing or ambiguous."
                ],
                persistence_behavior=[],
            )
        )

    if state_ids:
        state_dependencies = [
            item
            for item in ("operly_interaction_verification", "operly_subject_identity")
            if any(node.node_id == item for node in nodes)
        ]
        nodes.append(
            CapabilityGraphNode(
                node_id="operly_state_transition",
                title="State transition enforcement",
                objective="Enforce the requested lifecycle transitions coherently.",
                responsibility="Validate and apply only allowed state transitions for the requested actions.",
                requirement_ids=state_ids,
                dependencies=state_dependencies,
                inputs=["validated action intent", "current subject state"],
                outputs=["accepted next state or explicit rejection"],
                invariants=[
                    "Only allowed transitions may change state.",
                    "Contradictory or impossible transitions cannot silently overwrite current state.",
                ],
                failure_cases=[
                    "Reject an invalid transition without partially mutating state."
                ],
                security_constraints=[],
                persistence_behavior=[],
            )
        )

    if durable_ids:
        dependencies = (
            ["operly_state_transition"]
            if any(node.node_id == "operly_state_transition" for node in nodes)
            else []
        )
        nodes.append(
            CapabilityGraphNode(
                node_id="operly_durable_state",
                title="Durable state recording",
                objective="Keep accepted state changes available for later reads and verification.",
                responsibility="Persist accepted state changes and authoritative timestamps before reporting success.",
                requirement_ids=durable_ids,
                dependencies=dependencies,
                inputs=["accepted state change", "authoritative timestamp"],
                outputs=["durable state or event record"],
                invariants=[
                    "A reported successful mutation must be reflected by subsequent reads."
                ],
                failure_cases=[
                    "Do not report success when the accepted state change cannot be durably recorded."
                ],
                security_constraints=[],
                persistence_behavior=[
                    "Persist accepted transitions and timestamps before reporting success."
                ],
            )
        )

    if access_ids:
        nodes.append(
            CapabilityGraphNode(
                node_id="operly_access_boundary",
                title="Restricted access boundary",
                objective="Protect restricted operations and views.",
                responsibility="Authenticate and authorize access to every restricted operation or private view.",
                requirement_ids=access_ids,
                dependencies=[],
                inputs=["actor identity", "requested restricted operation"],
                outputs=["authorized request or explicit denial"],
                invariants=[
                    "Restricted data and operations are unavailable without the required authority."
                ],
                failure_cases=["Deny access when authentication or authorization fails."],
                security_constraints=[
                    "Fail closed for restricted operations and private data."
                ],
                persistence_behavior=[],
            )
        )

    return nodes


def compile_platform_graph(
    analysis: RequirementsAnalysis,
    graph: CapabilityGraph | None = None,
) -> CapabilityGraph:
    """Merge canonical platform obligations into a requirement-owned graph."""
    base = graph or compile_requirement_graph(analysis)
    compiled = _platform_obligation_nodes(analysis)
    compiled_by_id = {node.node_id: node for node in compiled}
    nodes: list[CapabilityGraphNode] = []

    for node in base.nodes:
        replacement = compiled_by_id.pop(node.node_id, None)
        nodes.append(replacement or node)

    # Keep the global graph bound authoritative. If a very large requirement ledger
    # already consumes the graph budget, validation will report that explicitly
    # instead of silently dropping user requirements.
    nodes.extend(compiled_by_id.values())
    return CapabilityGraph(nodes=nodes)


def _requirement_semantic_owner_exists(requirement, graph: CapabilityGraph) -> bool:
    requirement_tokens = _semantic_tokens(
        " ".join(
            [
                requirement.source_excerpt,
                requirement.normalized_requirement,
                requirement.category,
                *requirement.acceptance_criteria,
            ]
        )
    )
    for node in graph.nodes:
        if requirement.requirement_id not in node.requirement_ids:
            continue
        node_tokens = _semantic_tokens(
            " ".join(
                [
                    node.title,
                    node.objective,
                    node.responsibility,
                    *node.inputs,
                    *node.outputs,
                ]
            )
        )
        if requirement_tokens & node_tokens:
            return True
        if node.node_id == f"requirement_{_slug(requirement.requirement_id, 'requirement')}":
            return True
    return False


def compiler_review(
    analysis: RequirementsAnalysis,
    graph: CapabilityGraph,
    deterministic_findings: list[str],
) -> GraphReview:
    """Conservative deterministic review used only when the model reviewer fails.

    It never repairs or invents owner decisions. It approves only a graph produced
    from the extracted ledger that still has direct owners for every mandatory item,
    all compiler obligations, and no deterministic contract errors.
    """
    missing = missing_mandatory_requirement_ids(graph, analysis)
    semantic_gaps: list[str] = []

    for requirement in analysis.requirements:
        if requirement.priority.lower() == "optional":
            continue
        if not _requirement_semantic_owner_exists(requirement, graph):
            semantic_gaps.append(
                f"{requirement.requirement_id} lacks a direct semantic implementation owner"
            )

    expected_obligations = {
        node.node_id: node.title for node in _platform_obligation_nodes(analysis)
    }
    present = {node.node_id for node in graph.nodes}
    for node_id, title in expected_obligations.items():
        if node_id not in present:
            semantic_gaps.append(f"Missing compiled platform obligation: {title}")

    approved = not deterministic_findings and not missing and not semantic_gaps
    return GraphReview(
        approved=approved,
        missing_requirement_ids=missing,
        dependency_issues=[],
        semantic_gaps=_unique(semantic_gaps),
        unnecessary_implementation_details=[],
        user_questions=[],
        reasoning_summary=(
            "OPERLY deterministic compiler verified requirement ownership and platform obligations."
            if approved
            else "OPERLY deterministic compiler found unresolved graph obligations."
        ),
    )


def _best_requirement_ids(
    analysis: RequirementsAnalysis,
    finding: str,
) -> list[str]:
    tokens = _semantic_tokens(finding)
    scored: list[tuple[int, str]] = []
    for requirement in analysis.requirements:
        req_tokens = _semantic_tokens(_requirement_text(requirement))
        score = len(tokens & req_tokens)
        if score:
            scored.append((score, requirement.requirement_id))
    scored.sort(reverse=True)
    if scored:
        best = scored[0][0]
        return [requirement_id for score, requirement_id in scored if score == best][:2]
    mandatory = [
        item.requirement_id
        for item in analysis.requirements
        if item.priority.lower() != "optional"
    ]
    return mandatory[:1]


def compile_review_findings(
    analysis: RequirementsAnalysis,
    graph: CapabilityGraph,
    review: GraphReview,
) -> CapabilityGraph:
    """Resolve review gaps deterministically when they map to known requirements.

    This is intentionally small and bounded. Unknown/ambiguous semantic gaps remain
    for the model repair turn rather than being guessed into the graph.
    """
    base = compile_platform_graph(analysis, graph)
    used = {node.node_id for node in base.nodes}
    nodes = list(base.nodes)

    for finding in review.semantic_gaps:
        finding_text = " ".join(str(finding or "").split()).strip()
        if not finding_text:
            continue
        linked = _best_requirement_ids(analysis, finding_text)
        if not linked:
            continue
        finding_tokens = _semantic_tokens(finding_text)
        linked_tokens = set()
        for requirement in analysis.requirements:
            if requirement.requirement_id in linked:
                linked_tokens.update(_semantic_tokens(_requirement_text(requirement)))
        if not (finding_tokens & linked_tokens):
            continue

        digest = hashlib.sha256(finding_text.lower().encode()).hexdigest()[:8]
        node_id = f"operly_review_gap_{digest}"
        if node_id in used:
            continue
        if len(nodes) >= 32:
            break
        used.add(node_id)
        lower = finding_text.lower()
        nodes.append(
            CapabilityGraphNode(
                node_id=node_id,
                title=finding_text[:120],
                objective=finding_text,
                responsibility=finding_text,
                requirement_ids=linked,
                dependencies=[],
                inputs=["validated inputs required by the linked requirements"],
                outputs=["implemented behavior resolving the semantic review gap"],
                invariants=[finding_text],
                failure_cases=[
                    "Surface an explicit failure instead of silently bypassing this required behavior."
                ],
                security_constraints=(
                    [finding_text]
                    if any(term in lower for term in _ACCESS_TERMS)
                    else []
                ),
                persistence_behavior=(
                    [finding_text]
                    if any(term in lower for term in _PERSISTENCE_TERMS + _STATE_TERMS)
                    else []
                ),
            )
        )
    return CapabilityGraph(nodes=nodes)


class CompilerGuidedPlanningOrchestrator(CoverageAwareGraphPlanningOrchestrator):
    """Requirements -> OPERLY compiler -> semantic review -> bounded repair.

    Normal path uses two model calls: requirement extraction and global semantic
    review. The graph itself is deterministic and therefore cannot fail merely
    because a second model emitted malformed graph JSON.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.compiler_review_fallbacks = 0
        self.compiler_repair_rounds = 0
        self.model_graph_repair_rounds = 0

    async def _review_or_compiler(
        self,
        analysis: RequirementsAnalysis,
        graph: CapabilityGraph,
        deterministic: list[str],
    ) -> GraphReview:
        try:
            return await self._review(analysis, graph, deterministic)
        except PlanningNeedsUserInput:
            raise
        except (PlanningBlocked, PlannerUnavailable):
            self.compiler_review_fallbacks += 1
            return compiler_review(analysis, graph, deterministic)

    @staticmethod
    def _deterministic_findings(
        graph: CapabilityGraph,
        analysis: RequirementsAnalysis,
    ) -> list[str]:
        return _unique(
            [
                *_graph_errors(graph, analysis),
                *semantic_claim_errors(graph, analysis),
            ]
        )

    async def run(self, prompt: str) -> dict[str, Any]:
        analysis = await self._requirements(prompt)

        # The model extracts meaning; OPERLY compiles the executable graph.
        graph = compile_platform_graph(analysis)
        deterministic = self._deterministic_findings(graph, analysis)
        review = await self._review_or_compiler(analysis, graph, deterministic)

        repair_rounds = 0
        if deterministic or not review.approved:
            # First repair is deterministic and targeted to known review findings.
            repair_rounds += 1
            self.compiler_repair_rounds += 1
            graph = compile_review_findings(analysis, graph, review)
            graph = restore_missing_requirements_as_leaf_nodes(graph, analysis)
            graph = compile_platform_graph(analysis, graph)
            deterministic = self._deterministic_findings(graph, analysis)
            review = await self._review_or_compiler(analysis, graph, deterministic)

        if deterministic or not review.approved:
            # Only now spend a planner call on the unresolved semantic delta.
            repair_rounds += 1
            inherited = self._review_findings(review, deterministic)
            try:
                graph = await self._plan_graph(
                    analysis,
                    current=graph,
                    findings=_semantic_repair_findings(
                        review,
                        deterministic,
                        inherited,
                    ),
                )
                self.model_graph_repair_rounds += 1
            except PlanningNeedsUserInput:
                raise
            except (PlanningBlocked, PlannerUnavailable):
                # Keep the last compiler-owned graph. Final validation below remains
                # strict; we do not fail solely because repair JSON was malformed.
                pass

            graph = normalize_semantic_requirement_ownership(graph, analysis)
            graph = restore_missing_requirements_as_leaf_nodes(graph, analysis)
            graph = compile_platform_graph(analysis, graph)
            deterministic = self._deterministic_findings(graph, analysis)
            review = await self._review_or_compiler(analysis, graph, deterministic)

        if deterministic:
            raise PlanningBlocked(
                "capability graph failed deterministic validation: "
                + "; ".join(deterministic[:20])
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
                + (
                    "; ".join(findings[:20])
                    if findings
                    else review.reasoning_summary
                )
            )

        nodes = _to_proposed_nodes(graph, analysis)
        validations = {
            node.node_id: _approved_node_validation()
            for node in nodes
        }
        return {
            "analysis": analysis,
            "nodes": nodes,
            "validations": validations,
            "global": _review_to_global(review),
            "budget": self.budget,
            "global_repair_rounds": repair_rounds,
            "dependency_resolution_traces": [],
            "planning_engine": PLANNING_ENGINE_VERSION,
            "expected_normal_model_calls": 2,
            "graph_repair_rounds": repair_rounds,
            "coverage_repair_rounds": 0,
            "compiler_review_fallbacks": self.compiler_review_fallbacks,
            "compiler_repair_rounds": self.compiler_repair_rounds,
            "model_graph_repair_rounds": self.model_graph_repair_rounds,
        }
