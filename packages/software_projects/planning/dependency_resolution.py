"""Recursive dependency resolution for live planning.

A dependency finding is actionable work, not a terminal planning state. This module
converts validator dependency findings into either a link to an already-ready plan
node or the smallest essential dependency contract, which is then fed back through
the same recursive validation machinery as every other node.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from packages.software_projects.planning.live_planning import (
    Contract,
    PlanningBlocked,
    PlanningContextPacket,
    ProposedNode,
    RequirementsAnalysis,
    ROLE_PROMPTS,
    ValidatorOutput,
    finding_records_for_node,
    structural_errors,
    scope_errors,
)


ROLE_PROMPTS.setdefault(
    "dependency_resolver",
    "Resolve only the supplied dependency findings. Prefer linking an existing plan "
    "node when it already satisfies the dependency. Otherwise define the smallest "
    "essential dependency contract needed by the blocked node. Do not broaden product "
    "scope, invent user requirements, or introduce unrelated infrastructure. New "
    "dependency nodes must link only supplied requirement IDs and must be independently "
    "testable contracts that can pass through the normal validator/refinement loop.",
)


class DependencyResolution(Contract):
    finding_id: str
    action: Literal["link_existing", "create_dependency"]
    existing_node_id: str | None = None
    dependency_node: ProposedNode | None = None
    rationale: str

    @model_validator(mode="after")
    def valid_shape(self):
        if self.action == "link_existing":
            if not self.existing_node_id or self.dependency_node is not None:
                raise ValueError("link_existing requires existing_node_id only")
        else:
            if self.dependency_node is None or self.existing_node_id is not None:
                raise ValueError("create_dependency requires dependency_node only")
        return self


class DependencyResolutionOutput(Contract):
    resolutions: list[DependencyResolution] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_findings(self):
        ids = [item.finding_id for item in self.resolutions]
        if len(ids) != len(set(ids)):
            raise ValueError("dependency finding IDs must be unique")
        return self


def dependency_findings(node: ProposedNode, verdict: ValidatorOutput) -> list[dict[str, str]]:
    return [
        {
            "finding_id": str(item["finding_id"]),
            "message": str(item["message"]),
        }
        for item in finding_records_for_node(node, verdict)
        if item.get("field") == "dependencies"
    ]


def _resolution_cycle_errors(
    blocked_node: ProposedNode,
    existing_nodes: list[ProposedNode],
    new_nodes: list[ProposedNode],
    output: DependencyResolutionOutput,
) -> list[str]:
    graph: dict[str, set[str]] = {
        node.node_id: set(node.dependencies)
        for node in existing_nodes
        if node.node_id != blocked_node.node_id
    }
    graph.update({node.node_id: set(node.dependencies) for node in new_nodes})
    selected_targets = {
        item.existing_node_id if item.action == "link_existing" else item.dependency_node.node_id
        for item in output.resolutions
    }
    graph[blocked_node.node_id] = set(blocked_node.dependencies) | selected_targets

    errors: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str, path: list[str]):
        if node_id in visiting:
            cycle_start = path.index(node_id) if node_id in path else 0
            cycle = path[cycle_start:] + [node_id]
            errors.append("dependency resolution introduced cycle: " + " -> ".join(cycle))
            return
        if node_id in visited or node_id not in graph:
            return
        visiting.add(node_id)
        for dependency_id in graph[node_id]:
            visit(dependency_id, [*path, node_id])
        visiting.remove(node_id)
        visited.add(node_id)

    visit(blocked_node.node_id, [])
    return list(dict.fromkeys(errors))


def validate_dependency_resolution(
    output: DependencyResolutionOutput,
    findings: list[dict[str, str]],
    blocked_node: ProposedNode,
    existing_nodes: list[ProposedNode],
    analysis: RequirementsAnalysis,
    budget,
) -> list[str]:
    errors: list[str] = []
    required_ids = {item["finding_id"] for item in findings}
    supplied_ids = {item.finding_id for item in output.resolutions}
    if required_ids - supplied_ids:
        errors.append(
            "dependency resolution omitted finding IDs: " + ", ".join(sorted(required_ids - supplied_ids))
        )
    if supplied_ids - required_ids:
        errors.append(
            "dependency resolution invented finding IDs: " + ", ".join(sorted(supplied_ids - required_ids))
        )

    existing_ids = {item.node_id for item in existing_nodes}
    requirement_ids = {item.requirement_id for item in analysis.requirements}
    new_nodes: list[ProposedNode] = []
    for item in output.resolutions:
        if item.action == "link_existing":
            if item.existing_node_id == blocked_node.node_id:
                errors.append(f"{item.finding_id}: blocked node cannot depend on itself")
            elif item.existing_node_id not in existing_ids:
                errors.append(f"{item.finding_id}: unknown existing dependency node {item.existing_node_id}")
        else:
            dependency = item.dependency_node
            assert dependency is not None
            if dependency.node_id == blocked_node.node_id:
                errors.append(f"{item.finding_id}: dependency node cannot reuse blocked node ID")
            if dependency.node_id in existing_ids:
                errors.append(f"{item.finding_id}: dependency node ID already exists: {dependency.node_id}")
            if not dependency.linked_requirement_ids:
                errors.append(f"{item.finding_id}: dependency node must retain requirement provenance")
            if not set(dependency.linked_requirement_ids) <= set(blocked_node.linked_requirement_ids):
                errors.append(f"{item.finding_id}: dependency expanded beyond blocked node requirements")
            new_nodes.append(dependency)

    if new_nodes:
        new_ids = [item.node_id for item in new_nodes]
        if len(new_ids) != len(set(new_ids)):
            errors.append("dependency resolution proposed duplicate node IDs")
        errors.extend(
            structural_errors(
                new_nodes,
                requirement_ids,
                analysis.global_exclusions,
                budget,
                external_node_ids=existing_ids | set(new_ids),
            )
        )
        linked_by_id = {item.requirement_id: item.model_dump(mode="json") for item in analysis.requirements}
        for dependency in new_nodes:
            linked = [linked_by_id[x] for x in dependency.linked_requirement_ids if x in linked_by_id]
            errors.extend(
                f"{dependency.node_id}: {message}" for message in scope_errors(dependency, linked)
            )

    errors.extend(_resolution_cycle_errors(blocked_node, existing_nodes, new_nodes, output))
    return errors


def apply_dependency_resolution(
    blocked_node: ProposedNode,
    output: DependencyResolutionOutput,
) -> tuple[ProposedNode, list[ProposedNode], list[dict[str, Any]]]:
    data = blocked_node.model_dump(mode="json")
    created: list[ProposedNode] = []
    traces: list[dict[str, Any]] = []
    dependencies = list(data["dependencies"])

    for item in output.resolutions:
        target = item.existing_node_id if item.action == "link_existing" else item.dependency_node.node_id
        if target not in dependencies:
            dependencies.append(target)
        if item.action == "create_dependency":
            created.append(item.dependency_node)
        traces.append(
            {
                "finding_id": item.finding_id,
                "action": item.action,
                "dependency_node_id": target,
                "rationale": item.rationale,
            }
        )

    data["dependencies"] = dependencies
    return ProposedNode.model_validate(data), created, traces


async def resolve_dependencies(
    orchestrator,
    *,
    analysis: RequirementsAnalysis,
    blocked_node: ProposedNode,
    verdict: ValidatorOutput,
    existing_nodes: list[ProposedNode],
) -> tuple[ProposedNode, list[ProposedNode], list[dict[str, Any]]]:
    findings = dependency_findings(blocked_node, verdict)
    if not findings:
        raise PlanningBlocked(f"{blocked_node.node_id}: dependency resolution requested without findings")

    context = PlanningContextPacket(
        role="dependency_resolver",
        untrusted_requirements={
            "linked": [
                item.model_dump(mode="json")
                for item in analysis.requirements
                if item.requirement_id in blocked_node.linked_requirement_ids
            ],
            "dependency_findings": findings,
            "exclusions": analysis.global_exclusions,
        },
        current_contract=blocked_node.model_dump(mode="json"),
        related_contracts={
            "existing_nodes": [
                {
                    "node_id": item.node_id,
                    "title": item.title,
                    "objective": item.objective,
                    "responsibilities": item.responsibilities,
                    "outputs": item.outputs,
                    "dependencies": item.dependencies,
                    "linked_requirement_ids": item.linked_requirement_ids,
                }
                for item in existing_nodes
                if item.node_id != blocked_node.node_id
            ]
        },
        constraints={
            "all_finding_ids_must_be_resolved": [item["finding_id"] for item in findings],
            "prefer_existing_dependency": True,
            "new_dependency_must_be_minimal": True,
            "new_dependency_requirement_ids_must_be_subset_of": blocked_node.linked_requirement_ids,
            "dependency_graph_must_be_acyclic": True,
            "do_not_add_user_requirements": True,
            "do_not_add_optional_scope": True,
            "remaining_calls": orchestrator.budget.max_model_calls - orchestrator.budget.calls,
        },
        previous_findings=[verdict.model_dump(mode="json")],
    )
    output = await orchestrator._call(
        "dependency_resolver",
        context,
        DependencyResolutionOutput,
        blocked_node.node_id,
    )
    errors = validate_dependency_resolution(
        output,
        findings,
        blocked_node,
        existing_nodes,
        analysis,
        orchestrator.budget,
    )
    if errors:
        raise PlanningBlocked(
            f"{blocked_node.node_id}: dependency resolution failed deterministic validation: "
            + "; ".join(errors[:20])
        )
    return apply_dependency_resolution(blocked_node, output)
