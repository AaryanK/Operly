"""Dependency-aware live planning controller.

This preserves the existing recursive planner semantics while replacing the old
terminal `resolve_dependency` branch with executable dependency resolution. New or
linked dependencies are validated through the same controller before the blocked
node is reconsidered.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from packages.software_projects.planning.dependency_resolution import resolve_dependencies
from packages.software_projects.planning.live_planning import (
    ContractExpansionOutput,
    ContractPatchOutput,
    GlobalValidatorOutput,
    LivePlanningOrchestrator,
    PartialContract,
    PlannerOutput,
    PlanningBlocked,
    PlanningContextPacket,
    PRESERVABLE_FIELDS,
    ProposedNode,
    RequirementPartitionOutput,
    RequirementsAnalysis,
    ValidatorOutput,
    accepted_partial_contract,
    apply_contract_patch,
    canonicalize_minimal_contract,
    contract_completeness,
    deterministic_readiness,
    finding_records_for_node,
    merge_preserved_contract,
    normalize_platform_default_dependencies,
    patchable_fields,
    scope_errors,
    structural_errors,
    validate_partition_output,
)


class DependencyResolvingPlanningOrchestrator(LivePlanningOrchestrator):
    """Runs the standard planner but resolves dependency findings recursively."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dependency_resolution_traces: list[dict[str, Any]] = []

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
        errors = structural_errors(root.nodes, req_ids, analysis.global_exclusions, self.budget)
        if errors:
            raise PlanningBlocked("structural validation failed: " + "; ".join(errors[:20]))

        known_plan_node_ids = {node.node_id for node in root.nodes}
        node_registry: dict[str, ProposedNode] = {node.node_id: node for node in root.nodes}
        final_nodes: list[ProposedNode] = []
        validations: dict[str, ValidatorOutput] = {}
        queue = [(node, 1, []) for node in root.nodes]
        ineffective_counts: dict[str, int] = {}
        refinement_counts: dict[str, int] = {}
        patch_attempts: dict[str, int] = {}
        dependency_attempts: dict[tuple[str, str], int] = {}
        last_finding_ids: dict[str, set[str]] = {}
        last_completeness: dict[str, int] = {}

        while queue:
            node, depth, history = queue.pop(0)
            node_registry[node.node_id] = node
            linked = [
                x.model_dump(mode="json")
                for x in analysis.requirements
                if x.requirement_id in node.linked_requirement_ids
            ]
            deterministic_scope_findings = scope_errors(node, linked)
            dependency_contracts = [
                node_registry[dependency_id].model_dump(mode="json")
                for dependency_id in node.dependencies
                if dependency_id in node_registry
            ]
            validator_context = PlanningContextPacket(
                role="validator",
                untrusted_requirements={"linked": linked, "exclusions": analysis.global_exclusions},
                current_contract=node.model_dump(mode="json"),
                related_contracts={"dependencies": dependency_contracts},
                constraints={
                    "parent_objective": analysis.root_objective,
                    "readiness_rule": "deterministic AND semantic",
                    "scope_authority_rule": "only explicit or essential derived scope may block readiness",
                    "deterministic_scope_findings": deterministic_scope_findings,
                    "declared_dependencies_are_supplied_in_related_contracts": True,
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
                        raise PlanningBlocked(
                            f"{node.node_id}: no findings resolved and no contract fields added"
                        )
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
                    "normalized_response_digest": hashlib.sha256(
                        json.dumps(validator_result.structured_output or {}, sort_keys=True).encode()
                    ).hexdigest(),
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
                dependency_records = [
                    x for x in finding_records if x.get("field") == "dependencies"
                ]
                if not dependency_records:
                    raise PlanningBlocked(
                        f"{node.node_id}: dependency resolution requested without dependency findings"
                    )
                active_nodes_by_id: dict[str, ProposedNode] = {x.node_id: x for x in final_nodes}
                active_nodes_by_id.update({queued_node.node_id: queued_node for queued_node, _, _ in queue})
                active_nodes_by_id[node.node_id] = node
                work_items = []
                for finding in dependency_records:
                    key = (node.node_id, str(finding["finding_id"]))
                    dependency_attempts[key] = dependency_attempts.get(key, 0) + 1
                    if dependency_attempts[key] > self.budget.max_refinements_per_node:
                        raise PlanningBlocked(
                            f"{node.node_id}: maximum dependency resolution attempts exceeded for {finding['finding_id']}"
                        )
                    work_item = {
                        "blocked_node_id": node.node_id,
                        "finding_id": finding["finding_id"],
                        "requirement_ids": node.linked_requirement_ids,
                        "state": "resolving",
                    }
                    self.dependency_work_items.append(work_item)
                    work_items.append(work_item)

                patched_node, created_dependencies, resolution_traces = await resolve_dependencies(
                    self,
                    analysis=analysis,
                    blocked_node=node,
                    verdict=verdict,
                    existing_nodes=list(active_nodes_by_id.values()),
                )
                resolved_by_finding = {item["finding_id"]: item for item in resolution_traces}
                for work_item in work_items:
                    trace = resolved_by_finding.get(str(work_item["finding_id"]))
                    if trace:
                        work_item["state"] = "resolved"
                        work_item["dependency_node_id"] = trace["dependency_node_id"]

                for dependency in created_dependencies:
                    known_plan_node_ids.add(dependency.node_id)
                    node_registry[dependency.node_id] = dependency
                node_registry[patched_node.node_id] = patched_node
                self.dependency_resolution_traces.append(
                    {
                        "blocked_node_id": node.node_id,
                        "resolutions": resolution_traces,
                        "created_node_ids": [item.node_id for item in created_dependencies],
                        "patched_dependencies": list(patched_node.dependencies),
                    }
                )
                self.correction_traces[-1]["dependency_resolution"] = self.dependency_resolution_traces[-1]

                target_ids = {item["dependency_node_id"] for item in resolution_traces}
                priority_existing = []
                remaining_queue = []
                for queued_item in queue:
                    if queued_item[0].node_id in target_ids:
                        priority_existing.append(queued_item)
                    else:
                        remaining_queue.append(queued_item)
                dependency_history = history + [
                    verdict.model_dump(mode="json"),
                    {"dependency_resolution": resolution_traces},
                ]
                created_items = [
                    (dependency, depth, dependency_history)
                    for dependency in created_dependencies
                ]
                queue = (
                    priority_existing
                    + created_items
                    + [(patched_node, depth, dependency_history)]
                    + remaining_queue
                )
                continue

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
                locked = {
                    name: getattr(node, name)
                    for name in PRESERVABLE_FIELDS
                    if name not in allowed_patch_fields
                }
                patch_context = PlanningContextPacket(
                    role="contract_patcher",
                    untrusted_requirements={"linked": linked, "exclusions": analysis.global_exclusions},
                    current_contract=node.model_dump(mode="json"),
                    related_contracts={
                        "parent_objective": analysis.root_objective,
                        "dependency_summaries": dependency_contracts,
                    },
                    constraints={
                        "unresolved_findings": finding_records,
                        "fields_to_patch": sorted(allowed_patch_fields),
                        "locked_accepted_fields": locked,
                        "immutable_fields": [
                            "node_id",
                            "objective",
                            "responsibilities",
                            "linked_requirement_ids",
                            "node_type",
                        ],
                    },
                    previous_findings=[verdict.model_dump(mode="json")],
                    budget={"remaining_calls": self.budget.max_model_calls - self.budget.calls},
                )
                patch = await self._call("contract_patcher", patch_context, ContractPatchOutput, node.node_id)
                patched = apply_contract_patch(node, patch, allowed_patch_fields)
                node_registry[patched.node_id] = patched
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
                    previous_findings=[verdict.model_dump(mode="json")],
                    budget={"remaining_calls": self.budget.max_model_calls - self.budget.calls},
                )
                replacement = await self._call(
                    "contract_expander", minimal_context, ContractExpansionOutput, node.node_id
                )
                minimal = canonicalize_minimal_contract(
                    normalize_platform_default_dependencies(replacement.node),
                    node.linked_requirement_ids,
                    verdict.irrelevant_scope_expansion,
                )
                minimal_errors = []
                if minimal.node_id != node.node_id:
                    minimal_errors.append("minimal replacement changed node ID")
                if minimal.responsibilities != [responsibility]:
                    minimal_errors.append("minimal replacement changed bounded responsibility")
                if set(minimal.linked_requirement_ids) != set(node.linked_requirement_ids):
                    minimal_errors.append("minimal replacement changed linked requirements")
                minimal_errors.extend(scope_errors(minimal, linked))
                if minimal_errors:
                    raise PlanningBlocked(
                        f"{node.node_id}: minimal replacement failed scope validation: "
                        + "; ".join(minimal_errors)
                    )
                node_registry[minimal.node_id] = minimal
                queue = [(minimal, depth, history + [verdict.model_dump(mode="json")])] + queue
                continue

            ready, findings = deterministic_readiness(node, verdict)
            if ready:
                final_nodes.append(node)
                node_registry[node.node_id] = node
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
                previous_findings=[verdict.model_dump(mode="json")],
                budget={
                    "remaining_calls": self.budget.max_model_calls - self.budget.calls,
                    "remaining_nodes": self.budget.max_nodes - len(final_nodes) - len(queue),
                },
            )
            partitioned = await self._call(
                "requirement_partitioner", partition_context, RequirementPartitionOutput, node.node_id
            )
            partition_errors = validate_partition_output(
                partitioned, set(node.linked_requirement_ids), finding_ids, accepted
            )
            if partition_errors:
                repair_context = partition_context.model_copy(deep=True)
                repair_context.current_contract = {
                    "proposed_partitions": partitioned.model_dump(mode="json")
                }
                repair_context.previous_findings = [
                    {"partition_validation_errors": partition_errors}
                ]
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
                        name: [
                            x
                            for x in getattr(part.preserved_contract, name)
                            if x in accepted.get(name, [])
                        ]
                        for name in PRESERVABLE_FIELDS
                    }
                    sanitized.append(
                        part.model_copy(update={"preserved_contract": PartialContract(**values)})
                    )
                partitioned = RequirementPartitionOutput(partitions=sanitized)
                partition_errors = validate_partition_output(
                    partitioned, set(node.linked_requirement_ids), finding_ids, accepted
                )
            if partition_errors:
                raise PlanningBlocked(
                    "requirement partition failed after bounded repair: "
                    + "; ".join(partition_errors[:20])
                )

            refined_nodes = []
            partition_node_ids = {x.partition_id for x in partitioned.partitions}
            allowed_dependency_ids = known_plan_node_ids | partition_node_ids
            for partition in partitioned.partitions:
                preserved = partition.preserved_contract
                expansion_context = PlanningContextPacket(
                    role="contract_expander",
                    untrusted_requirements={
                        "linked": [
                            x for x in linked if x["requirement_id"] in partition.linked_requirement_ids
                        ],
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
                                x
                                for x in readiness_records
                                if x["finding_id"] in partition.addressed_finding_ids
                            ]
                        }
                    ],
                    budget={"remaining_calls": self.budget.max_model_calls - self.budget.calls},
                )
                expanded = await self._call(
                    "contract_expander",
                    expansion_context,
                    ContractExpansionOutput,
                    f"{node.node_id}:{partition.partition_id}",
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
                    expansion_errors.append(
                        "unknown dependency node IDs: " + ", ".join(sorted(unknown_dependencies))
                    )
                if expansion_errors:
                    repair_context = expansion_context.model_copy(deep=True)
                    repair_context.current_contract = {
                        "partition": partition.model_dump(mode="json"),
                        "proposed_expansion": expanded.model_dump(mode="json"),
                    }
                    repair_context.previous_findings = [
                        {"contract_validation_errors": expansion_errors}
                    ]
                    repair_context.constraints = {
                        **expansion_context.constraints,
                        "repair_only": True,
                    }
                    expanded = await self._call(
                        "contract_expander",
                        repair_context,
                        ContractExpansionOutput,
                        f"{node.node_id}:{partition.partition_id}",
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
                        expansion_errors.append(
                            "unknown dependency node IDs: "
                            + ", ".join(sorted(unknown_dependencies))
                        )
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
                child_linked = [
                    x for x in linked if x["requirement_id"] in child.linked_requirement_ids
                ]
                errors.extend(
                    f"{child.node_id}: {message}"
                    for message in scope_errors(child, child_linked)
                )
            if errors:
                raise PlanningBlocked(
                    "refinement structural validation failed: " + "; ".join(errors[:20])
                )
            known_plan_node_ids.update(x.node_id for x in refined_nodes)
            for child in refined_nodes:
                node_registry[child.node_id] = child
            if not refined_nodes:
                raise PlanningBlocked(f"{node.node_id}: empty refinement")
            merely_restates = all(
                set(x.responsibilities) == set(node.responsibilities)
                and contract_completeness(x) <= contract_completeness(node)
                for x in refined_nodes
            )
            if merely_restates and len(node.responsibilities) > 1:
                raise PlanningBlocked(
                    f"{node.node_id}: partition did not reduce multiple responsibilities"
                )
            queue = [
                (
                    child,
                    depth + 1,
                    history
                    + [
                        verdict.model_dump(mode="json"),
                        {"partition_id": part.partition_id},
                    ],
                )
                for child, part in zip(refined_nodes, partitioned.partitions)
            ] + queue
            if len(final_nodes) + len(queue) > self.budget.max_nodes:
                raise PlanningBlocked("maximum node count exceeded")

        global_context = PlanningContextPacket(
            role="global_validator",
            untrusted_requirements=analysis.model_dump(mode="json"),
            current_contract={
                "leaf_summaries": [x.model_dump(mode="json") for x in final_nodes]
            },
            constraints={
                "all_deterministic_ready": True,
                "explicit_exclusions": analysis.global_exclusions,
            },
        )
        global_result = await self._call(
            "global_validator", global_context, GlobalValidatorOutput
        )
        return {
            "analysis": analysis,
            "nodes": final_nodes,
            "validations": validations,
            "global": global_result,
            "budget": self.budget,
            "dependency_resolution_traces": list(self.dependency_resolution_traces),
        }
