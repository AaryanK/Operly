from __future__ import annotations

from packages.capabilities.business_provider import UnifiedBusinessProvider
from packages.plugins import EventSpec


class EventfulUnifiedBusinessProvider(UnifiedBusinessProvider):
    """Business capabilities plus the domain events they may durably emit."""

    events = (
        EventSpec(
            id="crm.contact.created",
            description="A new contact was added to the Operly CRM.",
            payload_schema={
                "type": "object",
                "properties": {
                    "contact_id": {"type": "string"},
                    "name": {"type": "string"},
                    "source": {"type": "string"},
                    "email": {"type": "string"},
                    "company": {"type": "string"},
                },
                "required": ["contact_id", "name", "source"],
                "additionalProperties": False,
            },
            scope="workspace",
            tags=frozenset({"crm", "contact", "created", "customer"}),
        ),
    )
