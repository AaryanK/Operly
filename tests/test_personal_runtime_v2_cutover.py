from pathlib import Path

from packages.business_brain.personal_agent import PERSONAL_SYSTEM_PROMPT, PersonalAgentService
from packages.business_brain.runtime_v2_catalog import domain_catalog_requests


ROOT = Path(__file__).resolve().parents[1]


def test_personal_agent_has_no_legacy_controller_or_discovery_loop():
    source = (ROOT / "packages/business_brain/personal_agent.py").read_text(encoding="utf-8")
    assert "AgentRunController" not in source
    assert "CapabilityDiscoveryProvider" not in source
    assert "capability.search" not in PERSONAL_SYSTEM_PROMPT
    assert "capability.describe" not in PERSONAL_SYSTEM_PROMPT
    assert "run_personal_runtime_v2(" in source
    assert '"runtime_controller": "agent_runtime_v2"' in source


def test_personal_service_registry_contains_only_executable_surface_providers():
    service = PersonalAgentService()
    ids = {definition.id for definition in service.registry.definitions()}
    assert "capability.search" not in ids
    assert "capability.expand" not in ids
    assert "capability.describe" not in ids
    assert "account.list_workspaces" in ids
    assert "account.workspace_execute" in ids
    assert "runtime.context" in ids


def test_shared_catalog_has_deterministic_personal_workspace_slice():
    requests = domain_catalog_requests("what workspaces do I own?")
    workspace = next(item for item in requests if item.get("namespace") == "account.")
    assert "account.list_workspaces" in workspace["preferred"]
    assert "scope." in workspace["alternate_namespaces"]


def test_personal_scope_prompt_still_denies_workspace_authority_promotion():
    assert "This conversation belongs to the person, never to a workspace." in PERSONAL_SYSTEM_PROMPT
    assert "selected/focused workspace is only a disambiguation hint" in PERSONAL_SYSTEM_PROMPT
    assert "account.workspace_execute is not a bypass" in PERSONAL_SYSTEM_PROMPT
