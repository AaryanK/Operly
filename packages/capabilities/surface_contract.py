"""CI contract for capability parity across Operly interaction surfaces."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from packages.capabilities.defaults import default_registry


REQUIRED_SURFACES = ("web", "discord", "primary_agent", "remote_api")
SURFACE_ADAPTERS = {
    "web": "apps/web/src/main.tsx",
    "discord": "packages/connectors/discord/bot_shared.py",
    "primary_agent": "packages/capabilities/agent_harness.py",
    "remote_api": "apps/api/agent_router.py",
}


@dataclass(frozen=True, slots=True)
class CapabilityDomainContract:
    name: str
    matches: Callable[[str], bool]
    surfaces: tuple[str, ...] = REQUIRED_SURFACES


CORE_DOMAIN_CONTRACTS = (
    CapabilityDomainContract("workspace", lambda item: item.startswith(("workspace.", "account."))),
    CapabilityDomainContract("context", lambda item: item.startswith("context.")),
    CapabilityDomainContract("actions", lambda item: item.startswith("actions.")),
    # Studio is a product surface over the canonical SoftwareProject runtime. It no
    # longer owns a parallel studio.* capability namespace.
    CapabilityDomainContract("studio", lambda item: item.startswith("software.")),
    CapabilityDomainContract("gmail", lambda item: item.startswith("gmail.")),
    CapabilityDomainContract("crm", lambda item: item.startswith("crm.")),
    CapabilityDomainContract("reminders", lambda item: item.startswith("reminders.")),
    CapabilityDomainContract(
        "connectors",
        lambda item: item.startswith(("gmail.", "calendar.", "discord.")),
    ),
)


def validate_surface_parity(repo_root: Path | None = None) -> list[str]:
    """Return contract violations; CI treats any returned row as a failure."""
    root = repo_root or Path(__file__).resolve().parents[2]
    errors: list[str] = []
    registry = default_registry()
    capability_ids = {definition.id for definition in registry.definitions()}

    missing_adapter_surfaces = [
        surface
        for surface in REQUIRED_SURFACES
        if not (root / SURFACE_ADAPTERS[surface]).exists()
    ]
    if missing_adapter_surfaces:
        errors.append(
            "missing surface adapters: " + ", ".join(sorted(missing_adapter_surfaces))
        )

    for domain in CORE_DOMAIN_CONTRACTS:
        matches = sorted(item for item in capability_ids if domain.matches(item))
        if not matches:
            errors.append(f"{domain.name}: no discoverable canonical capability")
        missing = sorted(set(REQUIRED_SURFACES) - set(domain.surfaces))
        if missing:
            errors.append(
                f"{domain.name}: missing surface declarations: {', '.join(missing)}"
            )

    return errors
