"""Global-validation repair controller for live recursive planning.

The global validator is not a terminal judge. When it finds cross-plan deficiencies,
this controller converts those findings into bounded repair directives, sends only
the affected work back through the existing local validator/refinement machinery,
and reruns global validation until the plan is approved or the repair budget is
exhausted.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from packages.custom_software.live_planning import (
    Contract,
    ContractExpansionOutput,
    ContractPatchOutput,
    GlobalValidatorOutput,
    LivePlanningOrchestrator,
    PartialContract,
    PlannerOutput,
    PlanningBlocked,
    PlanningContextPacket,
    ProposedNode,
    RequirementPartitionOutput,
    RequirementsAnalysis,
    ROLE_PROMPTS,
    ValidatorOutput,
    PRESERVABLE_FIELDS,
    accepted_partial_contract,
    apply_contract_patch,
    canonicalize_minimal_contract,
    contract_completeness,
    deterministic_readiness,
    finding_records_for_node,
    merge_preserved_contract,
    normalize_platform_default_dependencies,
    normalized_plan_digest,
    patchable_fields,
    scope_errors,
    structural_errors,
    validate_partition_output,
)


ROLE_PROMPTS.setdefault(
    "global_repair_planner",
    "Convert global-validator findings into the smallest bounded repair directives. "
    "Do not revalidate the plan and do not rewrite unaffected ready nodes. Use "
    "revalidate when an existing leaf must be reconsidered, add only when a required "
    "subsystem/integration/journey/requirement has no existing leaf, and prune only "
    "for concepts explicitly identified as irrelevant by the global validator. Every "
    "finding ID must be addressed. Proposed nodes may only link supplied requirement "
    "IDs and may introduce only explicit or technically essential derived scope.",
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
    records: list[dict[str, str]] = []
    from packages.custom_software.live_planning import _finding_id

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
    covered: set[str] = set()
    existing_ids = {node.node_id for node in current_nodes}
    req_ids = {item.requirement_id for item in analysis.requirements}
    pruned: set[str] = set()
    revalidated: set[str] = set()
    additions: list[ProposedNode] = []

    for directive in output.directives:
        supplied = set(directive.finding_ids)
        unknown = supplied - required_finding_ids
        if unknown:
            errors.append(f"{directive.directive_id}: unknown global finding IDs: {', '.join(sorted(unknown))}")
        covered.update(supplied & required_finding_ids)

        if directive.action in {"revalidate", "prune"}:
            unknown_targets = set(directive.target_node_ids) - existing_ids
            if unknown_targets:
                errors.append(f"{directive.directive_id}: unknown target node IDs: {', '.join(sorted(unknown_targets))}")

        if directive.action == "revalidate":
            revalidated.update(directive.target_node_ids)
        elif directive.action == "prune":
            pruned.update(directive.target_node_ids)
            categories = {finding_by_id[x]["category"] for x in supplied if x in finding_by_id}
            if categories - {"irrelevant_concepts"}:
                errors.append(f"{directive.directive_id}: prune is allowed only for irrelevant_concepts findings")
        else:
            additions.extend(directive.proposed_nodes)

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

    if additions:
        errors.extend(
            structural_errors(
                additions,
                req_ids,
                analysis.global_exclusions,
                budget,
                external_node_ids=existing_ids | set(addition_ids),
            )
        )
        linked = {item.requirement_id: item.model_dump(mode="json") for item in analysis.requirements}
        for node in additions:
            relevant = [linked[x] for x in node.linked_requirement_ids if x in linked]
            errors.extend(f"{node.node_id}: {message}" for message in scope_errors(node, relevant))

    remaining_count = len(current_nodes) - len(pruned) + len(additions)
    if remaining_count <= 0:
        errors.append("global repair cannot remove every plan node")
    if remaining_count > budget.max_nodes:
        errors.append("global repair exceeds maximum node count")
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
    """Live planner whose global-validator failures become bounded repair work."""

    def __init__(self, *args, max_global_repair_rounds: int = 3, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_global_repair_rounds = max(1, min(max_global_repair_rounds, 5))
        self.global_repair_traces: list[dict[str, Any]] = []

    async def _validate_to_leaves(
        self,
        analysis: RequirementsAnalysis,
        root_nodes: list[ProposedNode],
        seed_histories: dict[str, list[dict[str, Any]]] | None,
        refinement_counts: dict[str, int],
        patch_attempts: dict[str, int],
    ) -> tuple[list[ProposedNode], dict[str, ValidatorOutput]]:
        req_ids = {x.requirement_id for x in analysis.requirements}
        errors = structural_errors(root_nodes, req_ids, analysis.global_exclusions, self.budget)
        if errors:
            raise PlanningBlocked("structural validation failed: " + "; ".join(errors[:20]))

        known_plan_node_ids = {node.node_id for node in root_nodes}
        final_nodes: list[ProposedNode] = []
        validations: dict[str, ValidatorOutput] = {}
        histories = seed_histories or {}
        queue = [(node, 1, list(histories.get(node.node_id, []))) for node in root_nodes]
        ineffective_counts: dict[str, int] = {}
        last_finding_ids: dict[str, set[str]] = {}
        last_completeness: dict[str, int] = {}

        while queue:
            node, depth, history = queue.pop(0)
            linked = [x.model_dump(mode="json") for x in analysis.requirements if x.requirement_id in node.linked_requirement_ids]
            deterministic_scope_findings = scope_errors(node, linked)
            validator_context = PlanningContextPacket(
                role="validator",
                untrusted_requirements={"linked": linked, "exclusions": analysis.global_exclusions},
                current_contract=node.model_dump(mode="json"),
                related_contracts={},
                constraints={
                    "parent_objective": analysis.root_objective,
                    "readiness_rule": "deterministic AND semantic",
                    "scope_authority_rule": "only explicit or essential derived scope may block readiness",
                    "deterministic_scope_findings": deterministic_scope_findings,
                    "prior_global_findings_are_unresolved_until_concretely_repaired": True,
                },
                previous_findings=history,
            )
            verdict = await self._call("validator", validator_context, ValidatorOutput, node.node_id)
            validations[node.node_id] = verdict
            if deterministic_scope_findings and verdict.disposition in {"approve", "decompose"}:
                verdict = verdict.model_copy(
                    update={
                        "disposition": "prune",
                        "ready_for_implementation": False,
                        "irrelevant_scope_expansion": list(
                            dict.fromkeys([*verdict.irrelevant_scope_expansion, *deterministic_scope_findings])
                        ),
                    }
                )
            nonblocking_choices = [
                x.subject
                for x in node.scope_claims
                if x.authority in {"implementation_choice", "optional_enhancement"}
            ]
            if depth >= self.budget.max_depth - 1 and nonblocking_choices and verdict.disposition == "decompose":
                verdict = verdict.model_copy(
                    update={
                        "disposition": "replace_with_minimal_contract",
                        "ready_for_implementation": False,
                        "irrelevant_scope_expansion": list(
                            dict.fromkeys([*verdict.irrelevant_scope_expansion, *nonblocking_choices])
                        ),
                        "minimal_contract_guidance": [
                            *verdict.minimal_contract_guidance,
                            "Collapse implementation choices to typed platform defaults",
                        ],
                    }
                )

            finding_records = finding_records_for_node(node, verdict)
            current_finding_ids = {str(x["finding_id"]) for x in finding_records}
            previous_ids = last_finding_ids.get(node.node_id)
            completeness = contract_completeness(node)
            resolved_ids = (previous_ids - current_finding_ids) if previous_ids is not None else set()
            new_ids = (current_finding_ids - previous_ids) if previous_ids is not None else set()
            if previous_ids is not None:
                structural_improvement = completeness > last_completeness.get(node.node_id, 0)
                if not resolved_ids and not structural_improvement:
                    ineffective_counts[node.node_id] = ineffective_counts.get(node.node_id, 0) + 1
                    if ineffective_counts[node.node_id] > self.budget.max_equivalent_decompositions:
                        raise PlanningBlocked(f"{node.node_id}: no findings resolved and no contract fields added")
                else:
                    ineffective_counts[node.node_id] = 0
            last_finding_ids[node.node_id] = current_finding_ids
            last_completeness[node.node_id] = completeness
            validator_result = self.results[-1][2]
            deterministic_findings = [
                x for x in finding_records if str(x["finding_id"]).startswith(("missing_", "multiple_"))
            ]
            self.correction_traces.append(
                {
                    "node_id": node.node_id,
                    "raw_model_response": validator_result.raw_response,
                    "parsed_structured_response": validator_result.structured_output,
                    "normalized_response_digest": normalized_plan_digest([node]),
                    "deterministically_merged_node": node.model_dump(mode="json"),
                    "deterministic_findings": deterministic_findings,
                    "llm_validator_findings": [x for x in finding_records if x not in deterministic_findings],
                    "resolved_finding_ids": sorted(resolved_ids),
                    "new_finding_ids": sorted(new_ids),
                }
            )

            if verdict.disposition == "ask_user" or verdict.requirement_conflicts:
                raise PlanningBlocked(
                    f"{node.node_id}: user input required: "
                    + "; ".join(verdict.requirement_conflicts or verdict.missing_information)
                )
            if verdict.disposition == "resolve_dependency" or verdict.missing_dependencies:
                for finding in [x for x in finding_records if x.get("field") == "dependencies"]:
                    self.dependency_work_items.append(
                        {
                            "blocked_node_id": node.node_id,
                            "finding_id": finding["finding_id"],
                            "requirement_ids": node.linked_requirement_ids,
                            "state": "queued",
                        }
                    )
                raise PlanningBlocked(f"{node.node_id}: dependency resolution work item queued")

            allowed_patch_fields = patchable_fields(node, verdict, finding_records)
            if len(node.responsibilities) == 1 and verdict.disposition in {"approve", "decompose"} and current_finding_ids:
                if allowed_patch_fields:
                    verdict = verdict.model_copy(update={"disposition": "patch_contract"})
                else:
                    raise PlanningBlocked(
                        f"{node.node_id}: atomic node has non-field deficiencies requiring explicit prune, dependency resolution, or user input"
                    )
            if len(node.responsibilities) > 1 and verdict.disposition == "patch_contract":
                verdict = verdict.model_copy(update={"disposition": "decompose"})

            if verdict.disposition == "patch_contract":
                patch_attempts[node.node_id] = patch_attempts.get(node.node_id, 0) + 1
                if patch_attempts[node.node_id] > self.budget.max_refinements_per_node:
                    raise PlanningBlocked(f"{node.node_id}: maximum contract patch attempts exceeded")
                locked = {name: getattr(node, name) for name in PRESERVABLE_FIELDS if name not in allowed_patch_fields}
                patch_context = PlanningContextPacket(
                    role="contract_patcher",
                    untrusted_requirements={"linked": linked, "exclusions": analysis.global_exclusions},
                    current_contract=node.model_dump(mode="json"),
                    related_contracts={"parent_objective": analysis.root_objective, "dependency_summaries": []},
                    constraints={
                        "unresolved_findings": finding_records,
                        "fields_to_patch": sorted(allowed_patch_fields),
                        "locked_accepted_fields": locked,
                        "immutable_fields": ["node_id", "objective", "responsibilities", "linked_requirement_ids", "node_type"],
                    },
                    previous_findings=[*history, verdict.model_dump(mode="json")],
                    budget={"remaining_calls": self.budget.max_model_calls - self.budget.calls},
                )
                patch = await self._call("contract_patcher", patch_context, ContractPatchOutput, node.node_id)
                patched = apply_contract_patch(node, patch, allowed_patch_fields)
                self.correction_traces[-1]["contract_patch"] = {
                    "claimed_resolved_finding_ids": patch.resolved_finding_ids,
                    "authorized_fields": sorted(allowed_patch_fields),
                    "patched_node": patched.model_dump(mode="json"),
                }
                queue = [(patched, depth, history + [verdict.model_dump(mode="json")])] + queue
                continue

            if verdict.disposition in {"prune", "replace_with_minimal_contract"}:
                refinement_counts[node.node_id] = refinement_counts.get(node.node_id, 0) + 1
                if refinement_counts[node.node_id] > self.budget.max_refinements_per_node:
                    raise PlanningBlocked(f"{node.node_id}: maximum scope simplifications exceeded")
                responsibility = (
                    node.responsibilities[0]
                    if len(node.responsibilities) == 1
                    else "Provide the minimal typed contract required by the linked requirements"
                )
                minimal_context = PlanningContextPacket(
                    role="contract_expander",
                    untrusted_requirements={"linked": linked, "exclusions": analysis.global_exclusions},
                    current_contract={
                        "node_id": node.node_id,
                        "title": node.title,
                        "objective": node.objective,
                        "required_responsibility": responsibility,
                    },
                    related_contracts={
                        "platform_defaults": {
                            "input_boundary": "typed internal model",
                            "storage_encoding": "runtime-profile default",
                            "network_protocol": "existing internal API convention",
                            "error_contract": "existing OPERLY typed error",
                        }
                    },
                    constraints={
                        "required_node_id": node.node_id,
                        "exactly_one_responsibility": responsibility,
                        "replace_with_minimal_contract": True,
                        "remove_scope": verdict.irrelevant_scope_expansion,
                        "minimal_contract_guidance": verdict.minimal_contract_guidance,
                        "linked_requirement_ids": node.linked_requirement_ids,
                        "do_not_add_unrequested_mechanisms": True,
                    },
                    previous_findings=[*history, verdict.model_dump(mode="json")],
                    budget={"remaining_calls": self.budget.max_model_calls - self.budget.calls},
                )
                replacement = await self._call("contract_expander", minimal_context, ContractExpansionOutput, node.node_id)
                minimal = canonicalize_minimal_contract(
                    normalize_platform_default_dependencies(replacement.node),
                    node.linked_requirement_ids,
                    verdict.irrelevant_scope_expansion,
                )
                minimal_errors: list[str] = []
                if minimal.node_id != node.node_id:
                    minimal_errors.append("minimal replacement changed node ID")
                if minimal.responsibilities != [responsibility]:
                    minimal_errors.append("minimal replacement changed bounded responsibility")
                if set(minimal.linked_requirement_ids) != set(node.linked_requirement_ids):
                    minimal_errors.append("minimal replacement changed linked requirements")
                minimal_errors.extend(scope_errors(minimal, linked))
                if minimal_errors:
                    raise PlanningBlocked(
                        f"{node.node_id}: minimal replacement failed scope validation: " + "; ".join(minimal_errors)
                    )
                queue = [(minimal, depth, history + [verdict.model_dump(mode="json")])] + queue
                continue

            ready, findings = deterministic_readiness(node, verdict)
            if ready:
                final_nodes.append(node)
                continue
            if depth >= self.budget.max_depth:
                raise PlanningBlocked(f"{node.node_id}: maximum depth reached: {findings}")
            refinement_counts[node.node_id] = refinement_counts.get(node.node_id, 0) + 1
            if refinement_counts[node.node_id] > self.budget.max_refinements_per_node:
                raise PlanningBlocked(f"{node.node_id}: maximum refinements exceeded")

            accepted = accepted_partial_contract(node, verdict)
            readiness_records = [
                {"finding_id": f"F-{index:03d}", "finding": finding}
                for index, finding in enumerate(findings, 1)
            ]
            finding_ids = {x["finding_id"] for x in readiness_records}
            partition_context = PlanningContextPacket(
                role="requirement_partitioner",
                untrusted_requirements={"linked": linked, "exclusions": analysis.global_exclusions},
                current_contract={
                    "node_id": node.node_id,
                    "title": node.title,
                    "objective": node.objective,
                    "responsibilities": node.responsibilities,
                },
                related_contracts={"accepted_partial_contract": accepted},
                constraints={
                    "depth": depth,
                    "readiness_findings": readiness_records,
                    "single_responsibility_required": True,
                    "partition_only": True,
                    "coverage_rule": "every linked requirement ID and finding_id must appear in at least one partition",
                },
                previous_findings=[*history, verdict.model_dump(mode="json")],
                budget={
                    "remaining_calls": self.budget.max_model_calls - self.budget.calls,
                    "remaining_nodes": self.budget.max_nodes - len(final_nodes) - len(queue),
                },
            )
            partitioned = await self._call("requirement_partitioner", partition_context, RequirementPartitionOutput, node.node_id)
            partition_errors = validate_partition_output(
                partitioned, set(node.linked_requirement_ids), finding_ids, accepted
            )
            if partition_errors:
                repair_context = partition_context.model_copy(deep=True)
                repair_context.current_contract = {"proposed_partitions": partitioned.model_dump(mode="json")}
                repair_context.previous_findings = [{"partition_validation_errors": partition_errors}]
                repair_context.constraints = {
                    **partition_context.constraints,
                    "repair_only": True,
                    "must_cover_requirement_ids": node.linked_requirement_ids,
                    "must_cover_finding_ids": sorted(finding_ids),
                }
                partitioned = await self._call(
                    "requirement_partitioner", repair_context, RequirementPartitionOutput, node.node_id
                )
                sanitized = []
                for part in partitioned.partitions:
                    values = {
                        name: [x for x in getattr(part.preserved_contract, name) if x in accepted.get(name, [])]
                        for name in PRESERVABLE_FIELDS
                    }
                    sanitized.append(part.model_copy(update={"preserved_contract": PartialContract(**values)}))
                partitioned = RequirementPartitionOutput(partitions=sanitized)
                partition_errors = validate_partition_output(
                    partitioned, set(node.linked_requirement_ids), finding_ids, accepted
                )
            if partition_errors:
                raise PlanningBlocked(
                    "requirement partition failed after bounded repair: " + "; ".join(partition_errors[:20])
                )

            refined_nodes: list[ProposedNode] = []
            partition_node_ids = {x.partition_id for x in partitioned.partitions}
            allowed_dependency_ids = known_plan_node_ids | partition_node_ids
            for partition in partitioned.partitions:
                preserved = partition.preserved_contract
                expansion_context = PlanningContextPacket(
                    role="contract_expander",
                    untrusted_requirements={
                        "linked": [x for x in linked if x["requirement_id"] in partition.linked_requirement_ids],
                        "exclusions": analysis.global_exclusions,
                    },
                    current_contract=partition.model_dump(mode="json"),
                    related_contracts={
                        "accepted_partial_contract": preserved.model_dump(mode="json"),
                        "parent_node": {"node_id": node.node_id, "objective": node.objective},
                    },
                    constraints={
                        "exactly_one_node": True,
                        "required_node_id": partition.partition_id,
                        "exactly_one_responsibility": partition.responsibility,
                        "preserve_values_exactly": True,
                        "allowed_dependency_node_ids": sorted(allowed_dependency_ids),
                        "dependency_rule": "dependencies contain node IDs only; use an empty list when no listed node is required",
                    },
                    previous_findings=[
                        {
                            "addressed_findings": [
                                x for x in readiness_records if x["finding_id"] in partition.addressed_finding_ids
                            ]
                        }
                    ],
                    budget={"remaining_calls": self.budget.max_model_calls - self.budget.calls},
                )
                expanded = await self._call(
                    "contract_expander", expansion_context, ContractExpansionOutput, f"{node.node_id}:{partition.partition_id}"
                )
                child = normalize_platform_default_dependencies(
                    merge_preserved_contract(expanded.node, preserved)
                )
                expansion_errors: list[str] = []
                if child.node_id != partition.partition_id:
                    expansion_errors.append("node_id must equal partition_id")
                if child.responsibilities != [partition.responsibility]:
                    expansion_errors.append("expansion changed partition responsibility")
                if set(child.linked_requirement_ids) != set(partition.linked_requirement_ids):
                    expansion_errors.append("expansion changed requirement partition")
                unknown_dependencies = set(child.dependencies) - allowed_dependency_ids
                if unknown_dependencies:
                    expansion_errors.append("unknown dependency node IDs: " + ", ".join(sorted(unknown_dependencies)))
                if expansion_errors:
                    repair_context = expansion_context.model_copy(deep=True)
                    repair_context.current_contract = {
                        "partition": partition.model_dump(mode="json"),
                        "proposed_expansion": expanded.model_dump(mode="json"),
                    }
                    repair_context.previous_findings = [{"contract_validation_errors": expansion_errors}]
                    repair_context.constraints = {**expansion_context.constraints, "repair_only": True}
                    expanded = await self._call(
                        "contract_expander", repair_context, ContractExpansionOutput, f"{node.node_id}:{partition.partition_id}"
                    )
                    child = normalize_platform_default_dependencies(
                        merge_preserved_contract(expanded.node, preserved)
                    )
                    expansion_errors = []
                    if child.node_id != partition.partition_id:
                        expansion_errors.append("node_id must equal partition_id")
                    if child.responsibilities != [partition.responsibility]:
                        expansion_errors.append("expansion changed partition responsibility")
                    if set(child.linked_requirement_ids) != set(partition.linked_requirement_ids):
                        expansion_errors.append("expansion changed requirement partition")
                    unknown_dependencies = set(child.dependencies) - allowed_dependency_ids
                    if unknown_dependencies:
                        expansion_errors.append("unknown dependency node IDs: " + ", ".join(sorted(unknown_dependencies)))
                if expansion_errors:
                    raise PlanningBlocked(
                        f"{partition.partition_id}: contract expansion failed after bounded repair: "
                        + "; ".join(expansion_errors)
                    )
                refined_nodes.append(child)

            errors = structural_errors(
                refined_nodes,
                req_ids,
                analysis.global_exclusions,
                self.budget,
                known_plan_node_ids,
            )
            for child in refined_nodes:
                child_linked = [x for x in linked if x["requirement_id"] in child.linked_requirement_ids]
                errors.extend(f"{child.node_id}: {message}" for message in scope_errors(child, child_linked))
            if errors:
                raise PlanningBlocked("refinement structural validation failed: " + "; ".join(errors[:20]))
            known_plan_node_ids.update(x.node_id for x in refined_nodes)
            if not refined_nodes:
                raise PlanningBlocked(f"{node.node_id}: empty refinement")
            merely_restates = all(
                set(x.responsibilities) == set(node.responsibilities)
                and contract_completeness(x) <= contract_completeness(node)
                for x in refined_nodes
            )
            if merely_restates and len(node.responsibilities) > 1:
                raise PlanningBlocked(f"{node.node_id}: partition did not reduce multiple responsibilities")
            queue = [
                (
                    child,
                    depth + 1,
                    history + [verdict.model_dump(mode="json"), {"partition_id": part.partition_id}],
                )
                for child, part in zip(refined_nodes, partitioned.partitions)
            ] + queue
            if len(final_nodes) + len(queue) > self.budget.max_nodes:
                raise PlanningBlocked("maximum node count exceeded")

        return final_nodes, validations

    async def run(self, prompt: str) -> dict[str, Any]:
        analyst_context = PlanningContextPacket(
            role="requirements_analyst",
            untrusted_requirements={"original_request": prompt},
            constraints={"no_architecture_design": True, "preserve_negation": True},
        )
        analysis = await self._call("requirements_analyst", analyst_context, RequirementsAnalysis)
        req_ids = {x.requirement_id for x in analysis.requirements}
        planner_context = PlanningContextPacket(
            role="planner",
            untrusted_requirements=analysis.model_dump(mode="json"),
            current_contract={"objective": analysis.root_objective},
            constraints={
                "global_exclusions": analysis.global_exclusions,
                "runtime": "OPERLY isolated generation",
                "no_templates": True,
            },
            budget=self.budget.__dict__ | {"started": None},
        )
        root = await self._call("planner", planner_context, PlannerOutput, "root")

        current_nodes = root.nodes
        seed_histories: dict[str, list[dict[str, Any]]] = {}
        refinement_counts: dict[str, int] = {}
        patch_attempts: dict[str, int] = {}
        validations: dict[str, ValidatorOutput] = {}

        for global_round in range(self.max_global_repair_rounds + 1):
            final_nodes, round_validations = await self._validate_to_leaves(
                analysis,
                current_nodes,
                seed_histories,
                refinement_counts,
                patch_attempts,
            )
            validations.update(round_validations)
            global_context = PlanningContextPacket(
                role="global_validator",
                untrusted_requirements=analysis.model_dump(mode="json"),
                current_contract={"leaf_summaries": [x.model_dump(mode="json") for x in final_nodes]},
                constraints={
                    "all_deterministic_ready": True,
                    "explicit_exclusions": analysis.global_exclusions,
                    "global_repair_round": global_round,
                },
            )
            global_result = await self._call("global_validator", global_context, GlobalValidatorOutput)
            if global_result.approved:
                return {
                    "analysis": analysis,
                    "nodes": final_nodes,
                    "validations": validations,
                    "global": global_result,
                    "budget": self.budget,
                    "global_repair_rounds": global_round,
                    "global_repair_traces": self.global_repair_traces,
                }

            findings = global_finding_records(global_result)
            if not findings:
                raise PlanningBlocked("global validator rejected the plan without actionable findings")
            if global_round >= self.max_global_repair_rounds:
                raise PlanningBlocked(
                    f"global validation remained unresolved after {self.max_global_repair_rounds} repair rounds"
                )

            repair_context = PlanningContextPacket(
                role="global_repair_planner",
                untrusted_requirements={
                    "requirements_analysis": analysis.model_dump(mode="json"),
                    "global_findings": findings,
                },
                current_contract={"ready_leaf_nodes": [x.model_dump(mode="json") for x in final_nodes]},
                related_contracts={"existing_node_ids": [x.node_id for x in final_nodes]},
                constraints={
                    "allowed_actions": ["revalidate", "add", "prune"],
                    "all_finding_ids_must_be_addressed": [x["finding_id"] for x in findings],
                    "allowed_requirement_ids": sorted(req_ids),
                    "preserve_unaffected_nodes_exactly": True,
                    "prune_only_irrelevant_concepts": True,
                    "do_not_create_new_user_requirements": True,
                    "remaining_calls": self.budget.max_model_calls - self.budget.calls,
                    "remaining_nodes": self.budget.max_nodes - len(final_nodes),
                },
                previous_findings=[global_result.model_dump(mode="json")],
            )
            repair = await self._call(
                "global_repair_planner",
                repair_context,
                GlobalRepairOutput,
                f"global-repair-{global_round + 1}",
            )
            repair_errors = validate_global_repair_output(
                repair,
                findings,
                final_nodes,
                analysis,
                self.budget,
            )
            if repair_errors:
                raise PlanningBlocked(
                    "global repair plan failed deterministic validation: " + "; ".join(repair_errors[:20])
                )

            repaired_nodes, seed_histories = apply_global_repair(repair, findings, final_nodes)
            self.global_repair_traces.append(
                {
                    "round": global_round + 1,
                    "global_findings": findings,
                    "repair": repair.model_dump(mode="json"),
                    "before_digest": normalized_plan_digest(final_nodes),
                    "after_seed_digest": normalized_plan_digest(repaired_nodes),
                }
            )
            current_nodes = repaired_nodes

        raise PlanningBlocked("global repair loop exhausted")
