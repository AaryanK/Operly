from types import SimpleNamespace

import pytest

from packages.actions.lifecycle import LifecycleStatus, lifecycle_truth, normalize_lifecycle_status
from packages.agents.runtime import AgentRuntime
from packages.capabilities.calendar_semantics_provider import CalendarSemanticsProvider
from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition
from packages.capabilities.gmail_read_provider import GmailReadProvider
from packages.capabilities.providers import BaseProvider
from packages.capabilities.registry import CapabilityRegistry
from packages.capabilities.session_view import SessionCapabilityView
from packages.capabilities.validation import PluginSchemaError, validate_arguments
from packages.capabilities.workflow_contracts import (
    AuthoritySource,
    WorkflowProposal,
    ProposalOperation,
    should_promote_workflow,
)
from packages.tasks.delivery import TaskDeliveryError, deliver_task_output


class DuplicateArgumentModel:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "dup-1",
                        "function": {
                            "name": "capability.search",
                            "arguments": '{"query":"one","query":"two"}',
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "repaired"}


@pytest.mark.asyncio
async def test_duplicate_json_property_never_reaches_capability_invoker():
    invoked = []

    async def schemas():
        return [
            {
                "type": "function",
                "function": {
                    "name": "capability.search",
                    "description": "search",
                    "parameters": {"type": "object"},
                },
            }
        ]

    async def invoke(name, arguments, call_id):
        invoked.append((name, arguments, call_id))
        return {"ok": True}

    result = await AgentRuntime(max_steps=3).run(
        model=DuplicateArgumentModel(),
        messages=[{"role": "user", "content": "search for something"}],
        schemas=schemas,
        invoke=invoke,
    )

    assert invoked == []
    assert result["message"] == "repaired"
    observation = result["trace"][0].observation
    assert observation["status"] == "INVALID_ARGUMENTS"
    assert observation["retryable"] is True
    assert observation["errors"] == [{"path": "query", "reason": "duplicate property"}]


def test_schema_validation_returns_repairable_field_errors():
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    with pytest.raises(PluginSchemaError) as captured:
        validate_arguments(schema, {})
    assert captured.value.as_errors() == [
        {"path": "query", "reason": "required property is missing"}
    ]


def test_internal_action_states_normalize_to_truthful_external_lifecycle():
    assert normalize_lifecycle_status("EXECUTING") == LifecycleStatus.RUNNING
    assert normalize_lifecycle_status("VERIFYING") == LifecycleStatus.RUNNING
    assert normalize_lifecycle_status("VERIFICATION_FAILED") == LifecycleStatus.UNVERIFIED
    assert normalize_lifecycle_status("REJECTED") == LifecycleStatus.CANCELLED
    assert lifecycle_truth("WAITING_APPROVAL")["completed"] is False
    assert lifecycle_truth("VERIFIED")["completed"] is True


class StageProvider(BaseProvider):
    name = "stage-test"
    capabilities = (
        CapabilityDefinition(
            "mail.read",
            "mail_read",
            "read",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("mail:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "mail.send",
            "mail_send",
            "send",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="high",
            permissions=("mail:send",),
            approval_policy=ApprovalPolicy.ALWAYS,
        ),
    )


def test_stage_exposure_hides_writes_until_execution():
    registry = CapabilityRegistry()
    registry.register(StageProvider())
    view = SessionCapabilityView(
        registry,
        "tenant",
        {"mail:read", "mail:send"},
        initial_ids={"mail.read", "mail.send"},
    )
    research_names = {row["function"]["name"] for row in view.schemas(stage="research")}
    execution_names = {row["function"]["name"] for row in view.schemas(stage="execution")}
    assert "mail.read" in research_names
    assert "mail.send" not in research_names
    assert "mail.send" in execution_names


def test_gmail_expanded_reads_are_read_only_capabilities():
    definitions = {item.id: item for item in GmailReadProvider.capabilities}
    assert set(definitions) == {
        "gmail.read_thread",
        "gmail.list_attachments",
        "gmail.read_attachment",
    }
    assert all(item.risk_level == "read_only" for item in definitions.values())
    assert all(item.approval_policy == ApprovalPolicy.AUTO for item in definitions.values())
    assert all(item.integration_provider == "google" for item in definitions.values())


@pytest.mark.asyncio
async def test_date_only_deadline_is_not_promoted_to_exact_conflict(monkeypatch):
    import packages.capabilities.calendar_semantics_provider as module

    async def connector(context, scope):
        return object()

    async def token(context, connector):
        return "token"

    async def request(*args, **kwargs):
        return {
            "items": [
                {
                    "id": "meeting",
                    "summary": "Meeting",
                    "start": {"dateTime": "2026-08-28T12:00:00-05:00"},
                    "end": {"dateTime": "2026-08-28T13:00:00-05:00"},
                    "status": "confirmed",
                }
            ]
        }

    monkeypatch.setattr(module, "google_connector_for_context", connector)
    monkeypatch.setattr(module, "google_access_token_for_context", token)
    monkeypatch.setattr(module, "request_json", request)
    result = await CalendarSemanticsProvider().execute(
        SimpleNamespace(db=None, tenant_id="tenant"),
        "calendar.assess_deadline_conflicts",
        {"deadline": "2026-08-28", "timezone": "America/Chicago"},
    )
    assert result.success is True
    assert result.evidence["assessment"] == "DATE_ONLY_NO_EXACT_CONFLICT"
    assert result.evidence["exact_conflicts"] == []
    assert len(result.evidence["events"]) == 1


def test_personal_workspace_authority_requires_explicit_delegation():
    with pytest.raises(ValueError):
        AuthoritySource(
            owner_type="personal",
            owner_id="user-1",
            delegated_to_workspace="workspace-1",
        )
    delegated = AuthoritySource(
        owner_type="personal",
        owner_id="user-1",
        delegated_to_workspace="workspace-1",
        delegation_id="delegation-1",
    )
    assert delegated.delegation_id == "delegation-1"


def test_workflow_promotion_and_proposal_validation():
    assert should_promote_workflow(dependent_steps=1) is False
    assert should_promote_workflow(dependent_steps=2) is True
    assert should_promote_workflow(needs_approval=True) is True
    proposal = WorkflowProposal(
        operations=[ProposalOperation("gmail.create_draft", {"to": ["a@example.com"]}, "low")],
        executable=True,
    )
    proposal.validate_for_execution()
    assert proposal.as_dict()["operations"][0]["capability"] == "gmail.create_draft"


@pytest.mark.asyncio
async def test_delivery_layer_requires_verified_receipt(monkeypatch):
    import packages.plugins as plugins
    import packages.tasks.delivery as delivery

    # This test isolates receipt truthfulness. Delayed-authority rechecks have their
    # own coverage and must not be weakened just to exercise adapter receipt logic.
    async def reauthorize(_target):
        return None

    monkeypatch.setattr(delivery, "_reauthorize_delivery_target", reauthorize)

    class Adapter:
        async def deliver(self, target, message):
            return {"status": "VERIFIED", "message_ids": ["m-1"]}

    class Runtime:
        def __init__(self, adapter):
            self.adapter = adapter

        def task_delivery_adapter(self, provider):
            return self.adapter

    monkeypatch.setattr(plugins, "default_plugin_runtime", lambda: Runtime(Adapter()))
    receipt = await deliver_task_output({"provider": "discord"}, "hello")
    assert receipt["status"] == "VERIFIED"
    assert receipt["message_ids"] == ["m-1"]
    assert receipt["provider"] == "discord"
    assert receipt["verified_at"]

    class UnverifiedAdapter:
        async def deliver(self, target, message):
            return None

    monkeypatch.setattr(plugins, "default_plugin_runtime", lambda: Runtime(UnverifiedAdapter()))
    with pytest.raises(TaskDeliveryError, match="task_delivery_unverified:discord"):
        await deliver_task_output({"provider": "discord"}, "hello")
