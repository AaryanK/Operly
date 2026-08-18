import json
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.company.events import query_events
from packages.database.business_models import CatalogItem, Contact, Lead
from packages.database.custom_software_models import GeneratedProject
from packages.database.models import Approval, Integration, Tenant, Task
from packages.database.connector_models import TenantConnector
from packages.database.business_models import Appointment, BusinessOrder, Quote
from packages.database.company_models import BusinessActionRecord
from packages.company.attention import attention_items


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
    operations: dict[str, Any] = field(default_factory=dict)
    attention: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]: return asdict(self)


async def get_company_state(tenant_id: str, db: AsyncSession) -> CompanyState:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None: raise LookupError("Tenant not found")
    offerings = (await db.scalars(select(CatalogItem).where(CatalogItem.tenant_id == tenant_id,
                                                            CatalogItem.active.is_(True)))).all()
    projects = (await db.scalars(select(GeneratedProject).where(GeneratedProject.tenant_id == tenant_id)
                                 .order_by(GeneratedProject.created_at.desc()))).all()
    integrations = (await db.scalars(select(Integration).where(Integration.tenant_id == tenant_id))).all()
    connectors=(await db.scalars(select(TenantConnector).where(TenantConnector.tenant_id==tenant_id))).all()
    events = await query_events(db, tenant_id, limit=25)
    leads=(await db.scalars(select(Lead).where(Lead.tenant_id==tenant_id).order_by(Lead.created_at.desc()).limit(50))).all()
    lead_stages={stage:sum(1 for x in leads if x.stage==stage) for stage in sorted({x.stage for x in leads})}
    tasks=(await db.scalars(select(Task).where(Task.tenant_id==tenant_id,Task.status!="completed").limit(25))).all()
    appointments=(await db.scalars(select(Appointment).where(Appointment.tenant_id==tenant_id,Appointment.status=="scheduled").order_by(Appointment.starts_at).limit(20))).all()
    quotes=(await db.scalars(select(Quote).where(Quote.tenant_id==tenant_id).order_by(Quote.created_at.desc()).limit(25))).all()
    orders=(await db.scalars(select(BusinessOrder).where(BusinessOrder.tenant_id==tenant_id).order_by(BusinessOrder.created_at.desc()).limit(20))).all()
    actions=(await db.scalars(select(BusinessActionRecord).where(BusinessActionRecord.tenant_id==tenant_id).order_by(BusinessActionRecord.created_at.desc()).limit(20))).all()
    attention=await attention_items(db,tenant_id)
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
        contact_channels=[{"provider":x.provider,"status":x.status,"enabled":x.enabled,"health":x.health_status,"account":x.provider_account_id} for x in connectors] or [{"provider": x.provider, "status": x.status} for x in integrations], goals=goals,
        constraints=constraints,
        digital_presence={"website": {"status": "preview" if latest else "absent", "url": f"/generated/{latest.slug}" if latest else None,
                                                "current_solution_id": latest.id if latest else None},
                          "social": [], "listings": [], "messaging": [x.provider for x in integrations if x.status == "connected"]},
        recent_activity=[{"id": e.id, "type": e.event_type, "occurred_at": e.occurred_at.isoformat(), "payload": e.payload} for e in events],
        metrics={"leads": await db.scalar(select(func.count(Lead.id)).where(Lead.tenant_id == tenant_id)) or 0,
                 "stale_leads":await db.scalar(select(func.count(Lead.id)).where(Lead.tenant_id==tenant_id,Lead.stage.not_in(["won","lost"]))) or 0,
                 "pending_approvals":await db.scalar(select(func.count(Approval.id)).where(Approval.tenant_id==tenant_id,Approval.status=="pending")) or 0,
                 "upcoming_appointments":await db.scalar(select(func.count(Appointment.id)).where(Appointment.tenant_id==tenant_id,Appointment.status=="scheduled")) or 0,
                 "contacts": await db.scalar(select(func.count(Contact.id)).where(Contact.tenant_id == tenant_id)) or 0},
        operations={"leads_by_stage":lead_stages,"actionable_leads":[{"id":x.id,"title":x.title,"stage":x.stage,"value":x.value,"next_action":x.next_action,"next_action_at":x.next_action_at.isoformat() if x.next_action_at else None} for x in leads if x.stage not in {"won","lost"}][:20],"open_tasks":[{"id":x.id,"title":x.title,"status":x.status} for x in tasks],"upcoming_appointments":[{"id":x.id,"title":x.title,"starts_at":x.starts_at.isoformat()} for x in appointments],"quotations_by_status":{s:sum(1 for x in quotes if x.status==s) for s in sorted({x.status for x in quotes})},"products_services_count":len(offerings),"inventory_warnings":[{"id":x.id,"name":x.name,"stock":x.stock_qty,"reorder_level":x.reorder_level} for x in offerings if x.item_type=="product" and x.stock_qty<=x.reorder_level][:20],"recent_orders":[{"id":x.id,"status":x.status,"total":x.total,"created_at":x.created_at.isoformat()} for x in orders],"recent_actions":[{"id":x.id,"capability":x.capability,"status":x.status,"created_at":x.created_at.isoformat()} for x in actions]},attention=attention)
