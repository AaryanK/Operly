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
        "software",
        "gmail",
        "crm",
        "reminders",
        "connectors",
    }
    assert REQUIRED_SURFACES == ("web", "discord", "primary_agent", "remote_api")
    assert validate_surface_parity() == []
