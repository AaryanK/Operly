from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.company.state import get_company_state


@dataclass(frozen=True, slots=True)
class CompanyContextRequest:
    tenant_id: str
    objective: str
    capability_scope: tuple[str, ...] = ()
    token_budget: int | None = None


@dataclass(slots=True)
class CompanyContext:
    company_summary: dict[str, Any]
    relevant_offerings: list[dict[str, Any]] = field(default_factory=list)
    relevant_channels: dict[str, Any] = field(default_factory=dict)
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    current_metrics: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    prior_decisions: list[dict[str, Any]] = field(default_factory=list)
    def to_dict(self): return asdict(self)


async def build_company_context(request: CompanyContextRequest, db: AsyncSession) -> CompanyContext:
    state = await get_company_state(request.tenant_id, db)
    objective = request.objective.lower()
    web_relevant = any(word in objective for word in ("website", "lead", "conversion", "local")) or "update_website" in request.capability_scope
    analytics_relevant = any(word in objective for word in ("lead", "measure", "analytic", "conversion")) or "read_analytics" in request.capability_scope
    event_terms = {"action."}
    if web_relevant: event_terms.add("website.")
    if analytics_relevant: event_terms.add("analytics.")
    recent = [e for e in state.recent_activity if any(e["type"].startswith(prefix) for prefix in event_terms)][:10]
    return CompanyContext(
        company_summary={"identity": state.identity, "brand": state.brand, "goals": state.goals},
        relevant_offerings=state.products_services[:10] if web_relevant else [],
        relevant_channels={"website": state.digital_presence["website"]} if web_relevant else {},
        recent_events=recent, current_metrics=state.metrics if analytics_relevant else {}, constraints=state.constraints,
        prior_decisions=[e for e in recent if e["type"] in {"action.approved", "action.rejected"}])
