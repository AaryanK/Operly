from types import SimpleNamespace

import pytest

from packages.agent_runtime import remote_harness as bridge
from packages.kernel.contracts import CapabilityRisk
from packages.security.execution_context import ScopeKind


def _context(scope=ScopeKind.WORKSPACE):
    return SimpleNamespace(
        scope_kind=scope,
        is_workspace=scope == ScopeKind.WORKSPACE,
        user_id="user-a",
        workspace_id="space-a" if scope == ScopeKind.WORKSPACE else None,
        conversation_id="conversation-a",
    )


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("OPERLY_DRAGONZPYDER_DELEGATION_KEY", "k" * 48)
    monkeypatch.setenv("OPERLY_DRAGONZPYDER_AGENT_TOKEN", "a" * 48)


def test_lease_only_includes_readonly_capabilities(keys):
    readonly = SimpleNamespace(id="calendar.list", risk=CapabilityRisk.READ_ONLY)
    mutation = SimpleNamespace(id="gmail.send", risk=CapabilityRisk.HIGH)
    value = bridge.issue_readonly_lease(
        _context(), run_id="run-1", capabilities=[readonly, mutation],
    )
    claims = bridge.validate_readonly_lease(value)
    assert claims["scope_kind"] == "workspace"
    assert claims["workspace_id"] == "space-a"
    assert claims["user_id"] == "user-a"
    assert claims["capability_ids"] == ["calendar.list"]
    assert bridge.validate_readonly_lease(value)["run_id"] == "run-1"


def test_personal_lease_cannot_inherit_workspace(keys):
    value = bridge.issue_readonly_lease(
        _context(ScopeKind.PERSONAL), run_id="run-2", capabilities=[],
    )
    claims = bridge.validate_readonly_lease(value)
    assert claims["scope_kind"] == "personal"
    assert claims["workspace_id"] is None


def test_tampering_and_expiry_fail_closed(keys, monkeypatch):
    value = bridge.issue_readonly_lease(
        _context(), run_id="run-3", capabilities=[],
    )
    with pytest.raises(PermissionError):
        bridge.validate_readonly_lease(value[:-1] + ("A" if value[-1] != "A" else "B"))
    clock = bridge.time.time()
    monkeypatch.setattr(bridge.time, "time", lambda: clock + 500)
    with pytest.raises(PermissionError):
        bridge.validate_readonly_lease(value)


def test_untrusted_endpoint_rejected(keys, monkeypatch):
    monkeypatch.setenv("OPERLY_DRAGONZPYDER_AGENT_URL", "https://attacker.example/internal/agents/operly/run")
    with pytest.raises(bridge.RemoteHarnessUnavailable):
        bridge.dragonzpyder_endpoint()
    monkeypatch.setenv("OPERLY_DRAGONZPYDER_AGENT_URL", "https://api.dragonzpyder.xyz/internal/agents/operly/run")
    assert bridge.dragonzpyder_endpoint().startswith("https://api.dragonzpyder.xyz/")
