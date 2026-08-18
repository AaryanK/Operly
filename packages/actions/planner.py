from dataclasses import asdict, dataclass
from typing import Any

from packages.company.context import CompanyContextRequest, build_company_context


@dataclass(frozen=True, slots=True)
class BusinessPlanNode:
    capability: str
    title: str
    arguments: dict[str, Any]
    rationale: str
    expected_outcome: str
    risk_level: str
    implementation_mode: str
    dependencies: tuple[str, ...] = ()


async def plan_business_objective(tenant_id: str, objective: str, db, registry):
    available = {definition.name for definition in registry.definitions()}
    requested = ("read_analytics", "update_website") if "lead" in objective.lower() else ("read_analytics",)
    context = await build_company_context(CompanyContextRequest(tenant_id, objective, requested), db)
    nodes = []
    for capability in requested:
        existing = capability in available
        nodes.append(BusinessPlanNode(
            capability=capability, title="Measure current lead activity" if capability == "read_analytics" else "Improve website lead conversion",
            arguments={} if capability == "read_analytics" else {"title": "Get a fast local service quote"},
            rationale="Establish a measurable baseline" if capability == "read_analytics" else "Give visitors a direct conversion-oriented call to action",
            expected_outcome="Current lead metrics are available" if capability == "read_analytics" else "Website preview presents a stronger lead call to action",
            risk_level="read_only" if capability == "read_analytics" else "medium",
            implementation_mode="existing_capability" if existing else "generated_solution",
            dependencies=() if capability == "read_analytics" else ("read_analytics",)))
    return {"objective": objective, "context": context.to_dict(), "nodes": [asdict(node) for node in nodes]}
