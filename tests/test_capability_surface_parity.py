from apps.api.main import app
from packages.capabilities.operation_parity import (
    GOVERNED_PREFIXES,
    operation_inventory,
    validate_operation_parity,
)
from packages.capabilities.surface_contract import (
    CORE_DOMAIN_CONTRACTS,
    REQUIRED_SURFACES,
    validate_surface_parity,
)


def test_core_capability_domains_have_cross_surface_contracts():
    assert {item.name for item in CORE_DOMAIN_CONTRACTS} == {
        "workspace",
        "context",
        "actions",
        "studio",
        "gmail",
        "crm",
        "reminders",
        "connectors",
    }
    assert REQUIRED_SURFACES == ("web", "discord", "primary_agent", "remote_api")
    assert validate_surface_parity() == []


def test_every_governed_api_operation_maps_to_capability_or_explicit_exemption():
    inventory = operation_inventory(app)
    assert inventory, GOVERNED_PREFIXES
    errors = validate_operation_parity(app)
    assert errors == [], "\n" + "\n".join(errors)
