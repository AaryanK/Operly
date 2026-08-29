from types import SimpleNamespace

import pytest

import packages.business_brain.runtime_v2 as runtime_v2_module


def test_runtime_v2_calendar_language_recognizes_appointments_and_conflicts():
    appointment_requests = runtime_v2_module._domain_catalog_requests(
        "Show today's appointments and identify scheduling conflicts. Do not modify anything."
    )
    namespaces = [row["namespace"] for row in appointment_requests]
    assert "calendar." in namespaces

    conflict_requests = runtime_v2_module._domain_catalog_requests(
        "Do I have any overlapping time slots today?"
    )
    assert "calendar." in [row["namespace"] for row in conflict_requests]


@pytest.mark.asyncio
async def test_runtime_v2_appointment_request_catalog_includes_calendar_list_events():
    calendar_row = {"id": "calendar.list_events"}
    unrelated_row = {"id": "ai.generate"}

    class Definition:
        description = "List Google Calendar events in a time window."
        risk_level = "read_only"
        input_schema = {
            "type": "object",
            "required": ["time_min", "time_max"],
        }

    class Registry:
        def search(self, _tenant_id, query, *, authority, limit):
            del authority, limit
            if query == "calendar.list_events":
                return [calendar_row]
            if query.startswith("calendar"):
                return [unrelated_row, calendar_row]
            return [unrelated_row]

        def definition(self, capability_id):
            assert capability_id == "calendar.list_events"
            return Definition()

        def availability(self, _tenant_id, capability_id, *, authority):
            del authority
            assert capability_id == "calendar.list_events"
            return SimpleNamespace(available=True, reason=None, next_action=None)

    class Harness:
        def capability_authorized(self, capability_id, _authority, _context):
            return capability_id == "calendar.list_events"

    catalog = await runtime_v2_module._compact_catalog(
        objective="Show today's appointments and identify scheduling conflicts. Do not modify anything.",
        tenant_id="tenant",
        authority={"calendar:read"},
        registry=Registry(),
        plugin_harness=Harness(),
        plugin_context=SimpleNamespace(),
    )

    assert [row["id"] for row in catalog] == ["calendar.list_events"]
    assert catalog[0]["required_fields"] == ["time_min", "time_max"]
