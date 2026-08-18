import json
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.company.events import query_events
from packages.database.business_models import CatalogItem, Contact, Lead
from packages.database.custom_software_models import GeneratedProject
from packages.database.models import Integration, Tenant


@dataclass(slots=True)
class CompanyState:
    identity: dict[str, Any]
    brand: dict[str, Any] = field(default_factory=dict)
    locations: list[dict[str, Any]] = field(default_factory=list)
    hours: dict[str, Any] = field(default_factory=dict)
    products_services: list[dict[str, Any]] = field(default_factory=list)
    audiences: list[dict[str, Any]] = field(default_factory=list)
    contact_channels: list[dict[str, Any]] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    digital_presence: dict[str, Any] = field(default_factory=dict)
    recent_activity: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]: return asdict(self)


async def get_company_state(tenant_id: str, db: AsyncSession) -> CompanyState:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None: raise LookupError("Tenant not found")
    offerings = (await db.scalars(select(CatalogItem).where(CatalogItem.tenant_id == tenant_id,
                                                            CatalogItem.active.is_(True)))).all()
    projects = (await db.scalars(select(GeneratedProject).where(GeneratedProject.tenant_id == tenant_id)
                                 .order_by(GeneratedProject.created_at.desc()))).all()
    integrations = (await db.scalars(select(Integration).where(Integration.tenant_id == tenant_id))).all()
    events = await query_events(db, tenant_id, limit=25)
    latest = projects[0] if projects else None
    brand = json.loads(latest.brand_json) if latest else {}
    goals, constraints = [], []
    for event in reversed(events):
        if event.event_type == "company.updated":
            goals = event.payload.get("goals", goals)
            constraints = event.payload.get("constraints", constraints)
    return CompanyState(
        identity={"id": tenant.id, "name": tenant.name, "timezone": tenant.timezone}, brand=brand,
        products_services=[{"id": x.id, "name": x.name, "type": x.item_type, "price": x.price} for x in offerings],
        contact_channels=[{"provider": x.provider, "status": x.status} for x in integrations], goals=goals,
        constraints=constraints,
        digital_presence={"website": {"status": "preview" if latest else "absent", "url": f"/generated/{latest.slug}" if latest else None,
                                                "current_solution_id": latest.id if latest else None},
                          "social": [], "listings": [], "messaging": [x.provider for x in integrations if x.status == "connected"]},
        recent_activity=[{"id": e.id, "type": e.event_type, "occurred_at": e.occurred_at.isoformat(), "payload": e.payload} for e in events],
        metrics={"leads": await db.scalar(select(func.count(Lead.id)).where(Lead.tenant_id == tenant_id)) or 0,
                 "contacts": await db.scalar(select(func.count(Contact.id)).where(Contact.tenant_id == tenant_id)) or 0})
