import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from packages.capabilities.contracts import CapabilityDefinition, CapabilityResult
from packages.database.business_models import Contact, Lead
from packages.database.custom_software_models import GeneratedProject


@dataclass(slots=True)
class ProviderContext:
    tenant_id: str
    db: Any


class BaseProvider:
    name = "base"
    capabilities: tuple[CapabilityDefinition, ...] = ()
    def supports(self, capability_name: str) -> bool: return any(x.name == capability_name for x in self.capabilities)


class OperlyAnalyticsProvider(BaseProvider):
    name = "operly_analytics"
    capabilities = (CapabilityDefinition("read_analytics", "Read tenant business metrics", {}, {"type": "object"}, "read_only", False),)
    async def execute(self, context, capability_name, arguments):
        leads = await context.db.scalar(select(func.count(Lead.id)).where(Lead.tenant_id == context.tenant_id)) or 0
        contacts = await context.db.scalar(select(func.count(Contact.id)).where(Contact.tenant_id == context.tenant_id)) or 0
        return CapabilityResult(True, False, {"leads": leads, "contacts": contacts, "source": "operly"})
    async def verify(self, context, capability_name, arguments, result):
        valid = result.success and {"leads", "contacts"}.issubset(result.evidence)
        return CapabilityResult(valid, False, {"metrics_observed": valid, **result.evidence})


class OperlyWebsiteProvider(BaseProvider):
    name = "operly_website"
    capabilities = (CapabilityDefinition("update_website", "Update the current Operly website preview", {"title": "string"}, {"solution_id": "string"}, "medium", True),)
    async def execute(self, context, capability_name, arguments):
        project = await context.db.scalar(select(GeneratedProject).where(GeneratedProject.tenant_id == context.tenant_id)
                                          .order_by(GeneratedProject.created_at.desc()))
        if project is None: return CapabilityResult(False, False, {"reason": "website_not_found"})
        brand = json.loads(project.brand_json or "{}")
        before = brand.get("title") or brand.get("name")
        title = str(arguments.get("title") or "Get a fast local service quote").strip()
        brand["title"] = title
        project.brand_json = json.dumps(brand, sort_keys=True)
        await context.db.flush()
        return CapabilityResult(True, before != title, {"solution_id": project.id, "before_title": before, "title": title}, project.id)
    async def verify(self, context, capability_name, arguments, result):
        if not result.success or not result.external_reference: return CapabilityResult(False, False, {"reason": "execution_failed"})
        project = await context.db.scalar(select(GeneratedProject).where(GeneratedProject.id == result.external_reference,
                                                                         GeneratedProject.tenant_id == context.tenant_id))
        actual = json.loads(project.brand_json or "{}").get("title") if project else None
        expected = str(arguments.get("title") or "Get a fast local service quote").strip()
        return CapabilityResult(actual == expected, result.changed, {"solution_id": result.external_reference, "expected_title": expected, "actual_title": actual})


class OperlyCRMProvider(BaseProvider):
    name = "operly_crm"
    capabilities = (CapabilityDefinition("create_lead", "Create an Operly CRM lead", {"title": "string"}, {"lead_id": "string"}, "low", True),)
    async def execute(self, context, capability_name, arguments):
        lead = Lead(tenant_id=context.tenant_id, title=str(arguments["title"]), stage="new", value=float(arguments.get("value", 0)))
        context.db.add(lead); await context.db.flush()
        return CapabilityResult(True, True, {"lead_id": lead.id}, lead.id)
    async def verify(self, context, capability_name, arguments, result):
        lead = await context.db.scalar(select(Lead).where(Lead.id == result.external_reference, Lead.tenant_id == context.tenant_id))
        return CapabilityResult(lead is not None, True, {"lead_id": result.external_reference, "record_exists": lead is not None})


def default_registry():
    from packages.capabilities.registry import CapabilityRegistry
    registry = CapabilityRegistry()
    for provider in (OperlyAnalyticsProvider(), OperlyWebsiteProvider(), OperlyCRMProvider()): registry.register(provider)
    return registry
