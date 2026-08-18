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
    context = await build_company_context(CompanyContextRequest(tenant_id, objective), db)
    return {"objective": objective, "context": context.to_dict(), "nodes": [],
            "available_plugins": [item.id for item in registry.definitions()],
            "planning_mode": "persistent_llm_plugin_loop",
            "instruction": "Use the business agent loop; deterministic code authorizes calls but does not choose strategy."}
