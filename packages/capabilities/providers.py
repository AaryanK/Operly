import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult, ExecutionMode
from packages.company.events import query_events
from packages.company.state import get_company_state
from packages.database.business_models import BusinessDocument, Contact, Lead
from packages.database.custom_software_models import GeneratedProject


@dataclass(slots=True)
class ProviderContext:
    tenant_id: str
    db: Any
    actor_id: str | None = None
    provider_config: dict[str, Any] | None = None
    execution_id: str | None = None


class BaseProvider:
    name = "base"
    capabilities: tuple[CapabilityDefinition, ...] = ()
    def supports(self, capability_name: str) -> bool: return any(x.id == capability_name or x.name == capability_name for x in self.capabilities)


class OperlyAnalyticsProvider(BaseProvider):
    name = "operly_analytics"
    capabilities = (CapabilityDefinition("analytics.query", "analytics_query", "Read tenant business metrics",
        {"type":"object","properties":{},"additionalProperties":False}, {"type": "object"}, risk_level="read_only",
        permissions=("analytics:read",), approval_policy=ApprovalPolicy.AUTO),)
    async def execute(self, context, capability_name, arguments):
        leads = await context.db.scalar(select(func.count(Lead.id)).where(Lead.tenant_id == context.tenant_id)) or 0
        contacts = await context.db.scalar(select(func.count(Contact.id)).where(Contact.tenant_id == context.tenant_id)) or 0
        return CapabilityResult(True, False, {"leads": leads, "contacts": contacts, "source": "operly"})
    async def verify(self, context, capability_name, arguments, result):
        valid = result.success and {"leads", "contacts"}.issubset(result.evidence)
        return CapabilityResult(valid, False, {"metrics_observed": valid, **result.evidence})


class OperlyWebsiteProvider(BaseProvider):
    name = "operly_website"
    capabilities = (
      CapabilityDefinition("website.inspect", "website_inspect", "Inspect the current Operly website preview",
        {"type":"object","properties":{},"additionalProperties":False}, {"type":"object"}, risk_level="read_only",
        permissions=("website:read",), approval_policy=ApprovalPolicy.AUTO),
      CapabilityDefinition("website.edit", "website_edit", "Edit the current Operly website preview",
        {"type":"object","properties":{"title":{"type":"string"}},"required":["title"],"additionalProperties":False},
        {"type":"object"}, risk_level="medium", permissions=("website:write",), reversible=True),)
    async def execute(self, context, capability_name, arguments):
        project = await context.db.scalar(select(GeneratedProject).where(GeneratedProject.tenant_id == context.tenant_id)
                                          .order_by(GeneratedProject.created_at.desc()))
        if project is None: return CapabilityResult(False, False, {"reason": "website_not_found"})
        brand = json.loads(project.brand_json or "{}")
        if capability_name == "website.inspect":
            return CapabilityResult(True, False, {"solution_id": project.id, "name": project.name, "brand": brand}, project.id)
        before = brand.get("title") or brand.get("name")
        title = str(arguments.get("title") or "Get a fast local service quote").strip()
        brand["title"] = title
        project.brand_json = json.dumps(brand, sort_keys=True)
        await context.db.flush()
        return CapabilityResult(True, before != title, {"solution_id": project.id, "before_title": before, "title": title}, project.id)
    async def verify(self, context, capability_name, arguments, result):
        if capability_name == "website.inspect":
            return CapabilityResult(result.success, False, {"website_observed": result.success,
                                                             "solution_id": result.external_reference})
        if not result.success or not result.external_reference: return CapabilityResult(False, False, {"reason": "execution_failed"})
        project = await context.db.scalar(select(GeneratedProject).where(GeneratedProject.id == result.external_reference,
                                                                         GeneratedProject.tenant_id == context.tenant_id))
        actual = json.loads(project.brand_json or "{}").get("title") if project else None
        expected = str(arguments.get("title") or "Get a fast local service quote").strip()
        return CapabilityResult(actual == expected, result.changed, {"solution_id": result.external_reference, "expected_title": expected, "actual_title": actual})


class OperlyCRMProvider(BaseProvider):
    name = "operly_crm"
    capabilities = (
      CapabilityDefinition("crm.create_lead", "crm_create_lead", "Create an Operly CRM lead",
        {"type":"object","properties":{"title":{"type":"string"},"value":{"type":"number"}},"required":["title"],"additionalProperties":False},
        {"type":"object"}, risk_level="low", permissions=("crm:write",), reversible=True),
      CapabilityDefinition("crm.search_leads", "crm_search_leads", "Find actionable or stale sales leads in this tenant",
        {"type":"object","properties":{"stale_days":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1,"maximum":50}},"additionalProperties":False},
        {"type":"object"}, risk_level="read_only", permissions=("crm:read",), approval_policy=ApprovalPolicy.AUTO),
      CapabilityDefinition("crm.update_lead", "crm_update_lead", "Update a lead next action",
        {"type":"object","properties":{"lead_id":{"type":"string"},"next_action":{"type":"string"}},"required":["lead_id","next_action"],"additionalProperties":False},
        {"type":"object"}, risk_level="low", permissions=("crm:write",), reversible=True))
    async def execute(self, context, capability_name, arguments):
        if capability_name == "crm.search_leads":
            from datetime import datetime, timedelta
            stale_days=max(0,int(arguments.get("stale_days",3))); limit=max(1,min(int(arguments.get("limit",20)),50))
            rows=(await context.db.scalars(select(Lead).where(Lead.tenant_id==context.tenant_id,
                Lead.stage.not_in(["won","lost"]),Lead.created_at<=datetime.utcnow()-timedelta(days=stale_days))
                .order_by(Lead.value.desc(),Lead.created_at).limit(limit))).all()
            return CapabilityResult(True,False,{"leads":[{"id":x.id,"title":x.title,"stage":x.stage,"value":x.value,
                "next_action":x.next_action,"created_at":x.created_at.isoformat()} for x in rows],"stale_days":stale_days})
        if capability_name == "crm.update_lead":
            lead=await context.db.scalar(select(Lead).where(Lead.id==str(arguments["lead_id"]),Lead.tenant_id==context.tenant_id))
            if not lead:return CapabilityResult(False,False,{"reason":"lead_not_found"})
            before=lead.next_action;lead.next_action=str(arguments["next_action"])[:2000];await context.db.flush()
            return CapabilityResult(True,before!=lead.next_action,{"lead_id":lead.id,"next_action":lead.next_action},lead.id)
        lead = Lead(tenant_id=context.tenant_id, title=str(arguments["title"]), stage="new", value=float(arguments.get("value", 0)))
        context.db.add(lead); await context.db.flush()
        return CapabilityResult(True, True, {"lead_id": lead.id}, lead.id)
    async def verify(self, context, capability_name, arguments, result):
        if capability_name == "crm.search_leads": return CapabilityResult(result.success,False,{"query_completed":result.success,"count":len(result.evidence.get("leads",[]))})
        lead = await context.db.scalar(select(Lead).where(Lead.id == result.external_reference, Lead.tenant_id == context.tenant_id))
        if capability_name == "crm.update_lead":
            valid=lead is not None and lead.next_action==str(arguments["next_action"])[:2000]
            return CapabilityResult(valid,result.changed,{"lead_id":result.external_reference,"next_action_persisted":valid})
        return CapabilityResult(lead is not None, True, {"lead_id": result.external_reference, "record_exists": lead is not None})


class CompanyProvider(BaseProvider):
    name="operly_company"
    capabilities=(
      CapabilityDefinition("company.read_state","company_read_state","Read bounded canonical company state",
        {"type":"object","properties":{},"additionalProperties":False},{"type":"object"},risk_level="read_only",permissions=("company:read",),approval_policy=ApprovalPolicy.AUTO),
      CapabilityDefinition("company.search_events","company_search_events","Search recent tenant business events",
        {"type":"object","properties":{"event_type":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":50}},"additionalProperties":False},
        {"type":"object"},risk_level="read_only",permissions=("company:read",),approval_policy=ApprovalPolicy.AUTO))
    async def execute(self,context,capability_name,arguments):
        if capability_name=="company.read_state": return CapabilityResult(True,False,(await get_company_state(context.tenant_id,context.db)).to_dict())
        rows=await query_events(context.db,context.tenant_id,event_type=arguments.get("event_type"),limit=min(int(arguments.get("limit",20)),50))
        return CapabilityResult(True,False,{"events":[{"type":x.event_type,"occurred_at":x.occurred_at.isoformat(),"payload":x.payload} for x in rows]})
    async def verify(self,context,capability_name,arguments,result): return CapabilityResult(result.success,False,{"observation_available":result.success})


class MessagingProvider(BaseProvider):
    name="operly_messaging"
    capabilities=(
      CapabilityDefinition("messaging.draft","messaging_draft","Create a personalized follow-up draft for a lead",
        {"type":"object","properties":{"lead_id":{"type":"string"},"message":{"type":"string"}},"required":["lead_id","message"],"additionalProperties":False},
        {"type":"object"},risk_level="low",permissions=("messaging:draft",),approval_policy=ApprovalPolicy.AUTO,reversible=True),)
    async def execute(self,context,capability_name,arguments):
        lead=await context.db.scalar(select(Lead).where(Lead.id==str(arguments["lead_id"]),Lead.tenant_id==context.tenant_id))
        if not lead:return CapabilityResult(False,False,{"reason":"lead_not_found"})
        kind="follow_up_draft";status="draft"
        row=BusinessDocument(tenant_id=context.tenant_id,title=f"Follow-up: {lead.title}",document_type=kind,
            content=str(arguments["message"])[:10000],status=status);context.db.add(row)
        lead.next_action=f"Follow-up {status}: {row.title}";await context.db.flush()
        return CapabilityResult(True,True,{"lead_id":lead.id,"document_id":row.id,"delivery_status":status},row.id)
    async def verify(self,context,capability_name,arguments,result):
        row=await context.db.scalar(select(BusinessDocument).where(BusinessDocument.id==result.external_reference,BusinessDocument.tenant_id==context.tenant_id))
        valid=row is not None and row.status==("draft" if capability_name=="messaging.draft" else "queued")
        return CapabilityResult(valid,result.changed,{"document_id":result.external_reference,"status":row.status if row else None,"persisted":valid})


class SolutionProvider(BaseProvider):
    name="operly_solution_harness"
    capabilities=(
      CapabilityDefinition("solution.inspect","solution_inspect","Inspect generated solutions available to this tenant",
        {"type":"object","properties":{},"additionalProperties":False},{"type":"object"},risk_level="read_only",
        permissions=("solution:read",),approval_policy=ApprovalPolicy.AUTO),
      CapabilityDefinition("solution.generate","solution_generate","Start the existing verified coding-harness planning path for a missing capability",
        {"type":"object","properties":{"requirement":{"type":"string"}},"required":["requirement"],"additionalProperties":False},
        {"type":"object"},risk_level="high",permissions=("solution:generate",),approval_policy=ApprovalPolicy.ALWAYS,
        execution_mode=ExecutionMode.ISOLATED_RUNNER))
    async def execute(self,context,capability_name,arguments):
        if capability_name=="solution.inspect":
            rows=(await context.db.scalars(select(GeneratedProject).where(GeneratedProject.tenant_id==context.tenant_id)
                .order_by(GeneratedProject.created_at.desc()).limit(20))).all()
            return CapabilityResult(True,False,{"solutions":[{"id":x.id,"name":x.name,"status":"available","architecture":x.architecture_pack} for x in rows]})
        if not context.actor_id:return CapabilityResult(False,False,{"reason":"authenticated_actor_required"})
        from packages.custom_software.plan_service import create_plan
        row,_,_=await create_plan(context.db,context.tenant_id,context.actor_id,str(arguments["requirement"])[:12000])
        return CapabilityResult(True,True,{"plan_id":row.id,"status":row.status,"next_step":"owner_review_and_approval"},row.id)
    async def verify(self,context,capability_name,arguments,result):
        if capability_name=="solution.inspect":return CapabilityResult(result.success,False,{"inventory_observed":result.success})
        from packages.database.custom_software_models import SoftwarePlanRecord
        row=await context.db.scalar(select(SoftwarePlanRecord).where(SoftwarePlanRecord.id==result.external_reference,
                                                                      SoftwarePlanRecord.tenant_id==context.tenant_id))
        return CapabilityResult(row is not None,result.changed,{"plan_id":result.external_reference,"persisted":row is not None,
                                                                 "status":row.status if row else None})


def default_registry(enabled_plugins=None):
    from packages.capabilities.registry import CapabilityRegistry
    def enabled(tenant,definition):return definition.integration_provider is None or enabled_plugins is None or definition.id in enabled_plugins
    registry = CapabilityRegistry(enabled_resolver=enabled)
    for provider in (CompanyProvider(), OperlyAnalyticsProvider(), OperlyWebsiteProvider(), OperlyCRMProvider(), MessagingProvider(), SolutionProvider()): registry.register(provider)
    from packages.connectors.google_provider import GmailProvider,GoogleCalendarProvider
    from packages.capabilities.message_curation import MessageCurationProvider
    registry.register(MessageCurationProvider());registry.register(GmailProvider());registry.register(GoogleCalendarProvider())
    return registry
