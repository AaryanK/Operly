"""Bounded global-validation repair layered over the existing live planner.

Global validation is a feedback stage, not a terminal judge. This module preserves
the accepted requirements analysis, turns global findings into bounded repair
directives, and re-enters the existing node validator/decompose/patch controller
with only the affected nodes carrying the global findings as unresolved context.
"""
from __future__ import annotations

from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, model_validator

from packages.custom_software.live_planning import (
    Contract,
    GlobalValidatorOutput,
    LivePlanningOrchestrator,
    PlannerOutput,
    PlanningBlocked,
    PlanningContextPacket,
    ProposedNode,
    RequirementsAnalysis,
    ROLE_PROMPTS,
    structural_errors,
    scope_errors,
    normalized_plan_digest,
)

T = TypeVar("T", bound=BaseModel)


ROLE_PROMPTS.setdefault(
    "global_repair_planner",
    "Convert global-validator findings into the smallest bounded repair directives. "
    "Do not revalidate the plan and do not rewrite unaffected ready nodes. Use "
    "revalidate when an existing leaf must be reconsidered; use add only when a "
    "required subsystem, integration, state machine, uncovered requirement, or user "
    "journey has no adequate leaf; use prune only for concepts explicitly identified "
    "as irrelevant by the global validator. Every finding ID must be addressed. "
    "Proposed nodes may link only supplied requirement IDs and may introduce only "
    "explicit or technically essential derived scope.",
)


GLOBAL_FINDING_FIELDS = (
    "missing_subsystems",
    "incompatible_interfaces",
    "missing_integrations",
    "missing_state_transitions",
    "uncovered_requirements",
    "superficial_tests",
    "irrelevant_concepts",
    "contradictions",
    "incomplete_user_journeys",
)

ADDABLE_GLOBAL_FINDINGS = {
    "missing_subsystems",
    "missing_integrations",
    "missing_state_transitions",
    "uncovered_requirements",
    "incomplete_user_journeys",
}


class GlobalRepairDirective(Contract):
    directive_id: str
    action: Literal["revalidate", "add", "prune"]
    finding_ids: list[str] = Field(min_length=1)
    target_node_ids: list[str] = []
    proposed_nodes: list[ProposedNode] = []
    rationale: str

    @model_validator(mode="after")
    def action_shape(self):
        if self.action == "add":
            if not self.proposed_nodes or self.target_node_ids:
                raise ValueError("add directives require proposed_nodes and no target_node_ids")
        else:
            if not self.target_node_ids or self.proposed_nodes:
                raise ValueError(f"{self.action} directives require target_node_ids and no proposed_nodes")
        return self


class GlobalRepairOutput(Contract):
    directives: list[GlobalRepairDirective] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_directives(self):
        ids = [item.directive_id for item in self.directives]
        if len(ids) != len(set(ids)):
            raise ValueError("global repair directive IDs must be unique")
        return self


def global_finding_records(result: GlobalValidatorOutput) -> list[dict[str, str]]:
    from packages.custom_software.live_planning import _finding_id

    records: list[dict[str, str]] = []
    for category in GLOBAL_FINDING_FIELDS:
        for message in getattr(result, category):
            records.append(
                {
                    "finding_id": _finding_id(f"global_{category}", message),
                    "category": category,
                    "message": message,
                }
            )
    return records


def validate_global_repair_output(
    output: GlobalRepairOutput,
    findings: list[dict[str, str]],
    current_nodes: list[ProposedNode],
    analysis: RequirementsAnalysis,
    budget,
) -> list[str]:
    errors: list[str] = []
    finding_by_id = {item["finding_id"]: item for item in findings}
    required_finding_ids = set(finding_by_id)
    existing_ids = {node.node_id for node in current_nodes}
    req_ids = {item.requirement_id for item in analysis.requirements}
    covered: set[str] = set()
    pruned: set[str] = set()
    revalidated: set[str] = set()
    additions: list[ProposedNode] = []

    for directive in output.directives:
        supplied = set(directive.finding_ids)
        unknown = supplied - required_finding_ids
        if unknown:
            errors.append(f"{directive.directive_id}: unknown global finding IDs: {', '.join(sorted(unknown))}")
        covered.update(supplied & required_finding_ids)
        categories = {finding_by_id[x]["category"] for x in supplied if x in finding_by_id}

        if directive.action in {"revalidate", "prune"}:
            unknown_targets = set(directive.target_node_ids) - existing_ids
            if unknown_targets:
                errors.append(f"{directive.directive_id}: unknown target node IDs: {', '.join(sorted(unknown_targets))}")

        if directive.action == "revalidate":
            revalidated.update(directive.target_node_ids)
        elif directive.action == "prune":
            pruned.update(directive.target_node_ids)
            if categories - {"irrelevant_concepts"}:
                errors.append(f"{directive.directive_id}: prune is allowed only for irrelevant_concepts findings")
        else:
            additions.extend(directive.proposed_nodes)
            if categories - ADDABLE_GLOBAL_FINDINGS:
                errors.append(
                    f"{directive.directive_id}: add is allowed only for missing subsystem/integration/state/requirement/journey findings"
                )

    missing = required_finding_ids - covered
    if missing:
        errors.append("global repair omitted finding IDs: " + ", ".join(sorted(missing)))
    conflicts = pruned & revalidated
    if conflicts:
        errors.append("nodes cannot be both pruned and revalidated: " + ", ".join(sorted(conflicts)))

    addition_ids = [node.node_id for node in additions]
    if len(addition_ids) != len(set(addition_ids)):
        errors.append("global repair proposed duplicate node IDs")
    collisions = set(addition_ids) & existing_ids
    if collisions:
        errors.append("global repair additions collide with existing node IDs: " + ", ".join(sorted(collisions)))

    prospective = [node for node in current_nodes if node.node_id not in pruned] + additions
    if not prospective:
        errors.append("global repair cannot remove every plan node")
    elif len(prospective) > budget.max_nodes:
        errors.append("global repair exceeds maximum node count")
    else:
        errors.extend(structural_errors(prospective, req_ids, analysis.global_exclusions, budget))
        linked = {item.requirement_id: item.model_dump(mode="json") for item in analysis.requirements}
        for node in additions:
            relevant = [linked[x] for x in node.linked_requirement_ids if x in linked]
            errors.extend(f"{node.node_id}: {message}" for message in scope_errors(node, relevant))
    return errors


def apply_global_repair(
    output: GlobalRepairOutput,
    findings: list[dict[str, str]],
    current_nodes: list[ProposedNode],
) -> tuple[list[ProposedNode], dict[str, list[dict[str, Any]]]]:
    finding_by_id = {item["finding_id"]: item for item in findings}
    pruned = {
        node_id
        for directive in output.directives
        if directive.action == "prune"
        for node_id in directive.target_node_ids
    }
    repaired = [node for node in current_nodes if node.node_id not in pruned]
    histories: dict[str, list[dict[str, Any]]] = {}

    for directive in output.directives:
        directive_findings = [finding_by_id[x] for x in directive.finding_ids if x in finding_by_id]
        history = {
            "global_repair_directive": directive.directive_id,
            "action": directive.action,
            "rationale": directive.rationale,
            "global_findings": directive_findings,
        }
        if directive.action == "revalidate":
            for node_id in directive.target_node_ids:
                histories.setdefault(node_id, []).append(history)
        elif directive.action == "add":
            for node in directive.proposed_nodes:
                repaired.append(node)
                histories.setdefault(node.node_id, []).append(history)
    return repaired, histories


class GlobalRepairPlanningOrchestrator(LivePlanningOrchestrator):
    """Turns failed global validation into bounded recursive repair rounds."""

    def __init__(self, *args, max_global_repair_rounds: int = 3, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_global_repair_rounds = max(1, min(max_global_repair_rounds, 5))
        self.global_repair_traces: list[dict[str, Any]] = []
        self._seed_analysis: RequirementsAnalysis | None = None
        self._seed_root: PlannerOutput | None = None
        self._seed_histories: dict[str, list[dict[str, Any]]] = {}

    async def _call(
        self,
        role: str,
        context: PlanningContextPacket,
        schema: type[T],
        node_id: str | None = None,
    ) -> T:
        if role == "requirements_analyst" and self._seed_analysis is not None:
            value = self._seed_analysis
            self._seed_analysis = None
            return schema.model_validate(value.model_dump(mode="json"))
        if role == "planner" and self._seed_root is not None:
            value = self._seed_root
            self._seed_root = None
            return schema.model_validate(value.model_dump(mode="json"))
        if role == "validator" and node_id and node_id in self._seed_histories:
            context = context.model_copy(deep=True)
            context.previous_findings = [*self._seed_histories[node_id], *context.previous_findings]
            context.constraints = {
                **context.constraints,
                "prior_global_findings_are_unresolved_until_concretely_repaired": True,
            }
        return await super()._call(role, context, schema, node_id)

    async def _rerun_existing_controller(
        self,
        prompt: str,
        analysis: RequirementsAnalysis,
        nodes: list[ProposedNode],
        histories: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        self._seed_analysis = analysis
        self._seed_root = PlannerOutput(nodes=nodes)
        self._seed_histories = histories
        try:
            return await super().run(prompt)
        finally:
            self._seed_analysis = None
            self._seed_root = None
            self._seed_histories = {}

    async def run(self, prompt: str) -> dict[str, Any]:
        outcome = await super().run(prompt)
        if outcome["global"].approved:
            outcome["global_repair_rounds"] = 0
            outcome["global_repair_traces"] = []
            return outcome

        for repair_round in range(1, self.max_global_repair_rounds + 1):
            analysis: RequirementsAnalysis = outcome["analysis"]
            current_nodes: list[ProposedNode] = outcome["nodes"]
            global_result: GlobalValidatorOutput = outcome["global"]
            findings = global_finding_records(global_result)
            if not findings:
                raise PlanningBlocked("global validator rejected the plan without actionable findings")

            repair_context = PlanningContextPacket(
                role="global_repair_planner",
                untrusted_requirements={
                    "requirements_analysis": analysis.model_dump(mode="json"),
                    "global_findings": findings,
                },
                current_contract={"ready_leaf_nodes": [x.model_dump(mode="json") for x in current_nodes]},
                related_contracts={"existing_node_ids": [x.node_id for x in current_nodes]},
                constraints={
                    "allowed_actions": ["revalidate", "add", "prune"],
                    "all_finding_ids_must_be_addressed": [x["finding_id"] for x in findings],
                    "allowed_requirement_ids": sorted(x.requirement_id for x in analysis.requirements),
                    "preserve_unaffected_nodes_exactly": True,
                    "prune_only_irrelevant_concepts": True,
                    "do_not_create_new_user_requirements": True,
                    "remaining_calls": self.budget.max_model_calls - self.budget.calls,
                    "remaining_nodes": self.budget.max_nodes - len(current_nodes),
                },
                previous_findings=[global_result.model_dump(mode="json")],
            )
            repair = await super()._call(
                "global_repair_planner",
                repair_context,
                GlobalRepairOutput,
                f"global-repair-{repair_round}",
            )
            repair_errors = validate_global_repair_output(
                repair,
                findings,
                current_nodes,
                analysis,
                self.budget,
            )
            if repair_errors:
                raise PlanningBlocked(
                    "global repair plan failed deterministic validation: " + "; ".join(repair_errors[:20])
                )

            repaired_nodes, histories = apply_global_repair(repair, findings, current_nodes)
            self.global_repair_traces.append(
                {
                    "round": repair_round,
                    "global_findings": findings,
                    "repair": repair.model_dump(mode="json"),
                    "before_digest": normalized_plan_digest(current_nodes),
                    "after_seed_digest": normalized_plan_digest(repaired_nodes),
                }
            )
            outcome = await self._rerun_existing_controller(prompt, analysis, repaired_nodes, histories)
            if outcome["global"].approved:
                outcome["global_repair_rounds"] = repair_round
                outcome["global_repair_traces"] = list(self.global_repair_traces)
                return outcome

        raise PlanningBlocked(
            f"global validation remained unresolved after {self.max_global_repair_rounds} repair rounds"
        )
