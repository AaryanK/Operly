from __future__ import annotations

from sqlalchemy import or_, select

from packages.capabilities.contracts import (
    ApprovalPolicy,
    CapabilityDefinition,
    CapabilityResult,
)
from packages.capabilities.providers import BaseProvider
from packages.database.business_models import Contact


class CRMReadProvider(BaseProvider):
    """Tenant-scoped CRM contact reads backed by the canonical contacts table."""

    name = "operly_crm_reads"
    capabilities = (
        CapabilityDefinition(
            "crm.list_contacts",
            "crm_list_contacts",
            "List CRM contacts in the current workspace, newest first.",
            {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("crm:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="crm",
            semantic_operations=frozenset(
                {
                    "list contacts",
                    "show contacts",
                    "who are my contacts",
                    "read crm contacts",
                }
            ),
        ),
        CapabilityDefinition(
            "crm.search_contacts",
            "crm_search_contacts",
            "Search CRM contacts in the current workspace by name, email, phone, or company.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 300},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("crm:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="crm",
            semantic_operations=frozenset(
                {
                    "find contact",
                    "search contacts",
                    "lookup customer",
                    "lookup crm contact",
                }
            ),
        ),
        CapabilityDefinition(
            "crm.get_contact",
            "crm_get_contact",
            "Read one CRM contact from the current workspace by contact ID.",
            {
                "type": "object",
                "properties": {
                    "contact_id": {"type": "string", "minLength": 1, "maxLength": 80},
                },
                "required": ["contact_id"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("crm:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="crm",
            semantic_operations=frozenset({"get contact", "read contact", "contact details"}),
        ),
    )

    @staticmethod
    def _row(contact: Contact) -> dict:
        return {
            "id": contact.id,
            "name": contact.name,
            "email": contact.email,
            "phone": contact.phone,
            "company": contact.company,
            "source": contact.source,
            "status": contact.status,
            "notes": contact.notes,
            "created_at": contact.created_at.isoformat() if contact.created_at else None,
        }

    async def execute(self, context, capability_name, arguments):
        limit = max(1, min(int(arguments.get("limit", 50)), 100))

        if capability_name == "crm.list_contacts":
            rows = list(
                (
                    await context.db.scalars(
                        select(Contact)
                        .where(Contact.tenant_id == context.tenant_id)
                        .order_by(Contact.created_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
            return CapabilityResult(
                True,
                False,
                {"contacts": [self._row(row) for row in rows], "count": len(rows)},
            )

        if capability_name == "crm.search_contacts":
            query = str(arguments.get("query") or "").strip()
            if not query:
                return CapabilityResult(False, False, {"reason": "query_required"})
            pattern = f"%{query}%"
            rows = list(
                (
                    await context.db.scalars(
                        select(Contact)
                        .where(
                            Contact.tenant_id == context.tenant_id,
                            or_(
                                Contact.name.ilike(pattern),
                                Contact.email.ilike(pattern),
                                Contact.phone.ilike(pattern),
                                Contact.company.ilike(pattern),
                            ),
                        )
                        .order_by(Contact.created_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
            return CapabilityResult(
                True,
                False,
                {
                    "contacts": [self._row(row) for row in rows],
                    "count": len(rows),
                    "query": query,
                },
            )

        if capability_name == "crm.get_contact":
            contact_id = str(arguments.get("contact_id") or "").strip()
            row = await context.db.scalar(
                select(Contact).where(
                    Contact.id == contact_id,
                    Contact.tenant_id == context.tenant_id,
                )
            )
            if row is None:
                return CapabilityResult(False, False, {"reason": "contact_not_found"})
            return CapabilityResult(True, False, {"contact": self._row(row)})

        return CapabilityResult(False, False, {"reason": "unsupported_crm_read_capability"})

    async def verify(self, context, capability_name, arguments, result):
        del context, capability_name, arguments
        return CapabilityResult(
            result.success,
            False,
            {"observation_available": result.success, **result.evidence},
            result.external_reference,
        )
