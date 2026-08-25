from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from packages.capabilities.computer_provider import AgentComputerProvider
from packages.capabilities.firewall import ActionBackedCapabilityFirewall, CapabilityInvocation
from packages.capabilities.defaults import default_registry
from packages.security.delegation import (
    DelegatedExecutionContext,
    delegate_execution_context,
    delegation_allows,
)
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind
from packages.service_bindings.contracts import BindingInvocation, ServiceBinding
from packages.service_bindings.service import CapabilityGateway


def workspace_context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id="tenant-a",
        user_id="user-1",
        membership_id="member-1",
        role="owner",
        permissions=frozenset({"crm:read", "calendar:read", "computer:execute", "files:process"}),
        channel="web",
        surface=SurfaceKind.WORKSPACE_PRIVATE,
        conversation_id="conversation-1",
        scope_kind=ScopeKind.WORKSPACE,
        focus_workspace_id="tenant-a",
    )


def test_delegation_is_exact_and_cannot_widen_parent_authority():
    base = workspace_context()
    project = delegate_execution_context(
        base,
        principal_kind="software_project",
        principal_id="project-1",
        capability_ids={"crm.search_contacts"},
        delegation_id_value="binding-1",
    )

    assert isinstance(project, DelegatedExecutionContext)
    assert project.principal_key == "software_project:project-1"
    assert delegation_allows(project, "crm.search_contacts") is True
    assert delegation_allows(project, "calendar.list_events") is False
    assert project.permissions == base.permissions
    assert project.user_id == base.user_id

    with pytest.raises(PermissionError, match="cannot widen"):
        delegate_execution_context(
            project,
            principal_kind="agent_run",
            principal_id="child-run-1",
            capability_ids={"crm.search_contacts", "calendar.list_events"},
            delegation_id_value="child-delegation-1",
        )


@pytest.mark.asyncio
async def test_firewall_rejects_capability_outside_delegated_scope_before_execution():
    definition = SimpleNamespace(
        id="calendar.list_events",
        permissions=(),
        approval_policy="auto",
    )

    class Registry:
        def definition(self, capability_id):
            assert capability_id == "calendar.list_events"
            return definition

        def resolve(self, *args, **kwargs):
            raise AssertionError("registry.resolve must not run after delegated-scope denial")

    delegated = delegate_execution_context(
        workspace_context(),
        principal_kind="software_project",
        principal_id="project-1",
        capability_ids={"crm.search_contacts"},
        delegation_id_value="binding-1",
    )
    result = await ActionBackedCapabilityFirewall(Registry()).invoke(
        CapabilityInvocation(
            capability_id="calendar.list_events",
            arguments={},
            objective="Read calendar",
        ),
        delegated,
    )

    assert result.status == "DENIED"
    assert result.ok is False
    assert "delegated principal scope" in str(result.error)
    assert result.authority["principal_id"] == "software_project:project-1"
    assert result.authority["delegation_id"] == "binding-1"


@pytest.mark.asyncio
async def test_capability_gateway_reduces_project_runtime_to_exact_binding_capability():
    binding = ServiceBinding(
        id="binding-1",
        project_id="project-1",
        workspace_id="tenant-a",
        semantic_name="customers",
        capability_id="crm.search_contacts",
        capability_version="1.0.0",
        binding_mode="capability_gateway",
        principal_scope="project_runtime",
        configuration={},
    )

    async def load_binding(binding_id: str):
        assert binding_id == "binding-1"
        return binding

    gateway = CapabilityGateway(load_binding, lambda workspace_id: object())
    observed = {}

    async def fake_invoke(_firewall, request, execution_context):
        observed["request"] = request
        observed["context"] = execution_context
        return SimpleNamespace(ok=True, status="VERIFIED")

    with patch.object(ActionBackedCapabilityFirewall, "invoke", new=fake_invoke):
        result = await gateway.invoke(
            BindingInvocation("binding-1", {"query": "Acme"}, "request-1"),
            execution_context=workspace_context(),
            project_id="project-1",
        )

    assert result.ok is True
    delegated = observed["context"]
    assert isinstance(delegated, DelegatedExecutionContext)
    assert delegated.principal_key == "software_project:project-1"
    assert delegated.delegated_capability_ids == frozenset({"crm.search_contacts"})
    assert observed["request"].capability_id == "crm.search_contacts"


class BashRunner:
    def __init__(self):
        self.payload = None

    async def execute(self, payload):
        self.payload = payload
        return {
            "ok": True,
            "exitCode": 0,
            "timedOut": False,
            "stdout": "ok",
            "stderr": "",
            "outputs": [],
            "isolation": "railway_sandbox_vm_v1",
            "network": "isolated",
        }


@pytest.mark.asyncio
async def test_computer_run_bash_is_only_an_ephemeral_projection_over_command_sandbox():
    runner = BashRunner()
    provider = AgentComputerProvider(runner=runner)
    provider._runner_inputs = AsyncMock(return_value=[])
    provider._persist_outputs = AsyncMock(return_value=[])
    context = SimpleNamespace(db=None, actor_id="user-1", invocation={})

    result = await provider.execute(
        context,
        "computer.run_bash",
        {"script": "printf 'hello\\n'", "timeout_seconds": 30},
    )

    assert result.success is True
    assert runner.payload["mode"] == "command"
    assert runner.payload["argv"] == ["bash", "-lc", "printf 'hello\\n'"]
    assert result.evidence["ephemeral"] is True
    assert result.evidence["network"] == "isolated"


def test_canonical_registry_exposes_bounded_bash_without_a_second_runtime():
    registry = default_registry()
    definition = registry.definition("computer.run_bash")
    assert registry.provider_name("computer.run_bash") == "operly_agent_computer"
    assert definition.execution_mode.value == "isolated_runner"
    assert definition.permissions == ("computer:execute", "files:process")
