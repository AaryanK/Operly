from __future__ import annotations

import pytest

import packages.agents.controller as controller_module
from packages.agents.capability_rescue import (
    attempt_capability_rescue,
    has_execution_evidence,
)
from packages.agents.controller import AgentRunController
from packages.agents.runtime import AgentTraceEntry
from packages.agents.verification import RunGoalVerification


class _SatisfiedVerifier:
    async def verify(self, **kwargs):
        del kwargs
        return RunGoalVerification(
            True,
            verified=("The requested governed operation has verified execution evidence.",),
            reason="business_os_agent_recovery_test",
        )


def test_denied_or_failed_attempts_are_not_execution_evidence():
    denied = AgentTraceEntry(
        "workspace.run_command",
        {"argv": ["npm", "test"]},
        {"ok": False, "status": "DENIED", "retryable": True},
    )
    failed = AgentTraceEntry(
        "crm.create_contact",
        {"name": "Acme"},
        {"success": False, "status": "FAILED"},
    )
    invalid = AgentTraceEntry(
        "calendar.create_event",
        {},
        {"status": "INVALID_ARGUMENTS"},
    )

    assert has_execution_evidence([denied]) is False
    assert has_execution_evidence([failed]) is False
    assert has_execution_evidence([invalid]) is False
    assert has_execution_evidence([denied, failed, invalid]) is False


def test_verified_running_and_approval_lifecycle_are_execution_evidence():
    verified = AgentTraceEntry(
        "crm.create_contact",
        {"name": "Acme"},
        {"ok": True, "status": "VERIFIED"},
    )
    running = AgentTraceEntry(
        "software.build",
        {"objective": "Build an app"},
        {"status": "RUNNING"},
    )
    waiting = AgentTraceEntry(
        "gmail.send_draft",
        {"draft_id": "draft-1"},
        {"status": "WAITING_APPROVAL"},
    )

    assert has_execution_evidence([verified]) is True
    assert has_execution_evidence([running]) is True
    assert has_execution_evidence([waiting]) is True


@pytest.mark.asyncio
async def test_capability_rescue_can_preserve_a_six_operation_business_bundle():
    candidates = [
        "workspace.list_files",
        "workspace.read_file",
        "workspace.write_file",
        "workspace.run_command",
        "workspace.verify_web_hosting",
        "software.build",
    ]
    calls: list[tuple[str, dict]] = []
    messages = [
        {"role": "user", "content": "Build and verify a hosted business application."},
        {"role": "assistant", "content": "I cannot do that with my current tools."},
    ]

    async def invoke(name, arguments, call_id):
        del call_id
        calls.append((name, arguments))
        if name == "capability.search":
            return {
                "ok": True,
                "status": "VERIFIED",
                "observation": {
                    "capabilities": [
                        {
                            "id": capability_id,
                            "authorized": True,
                            "display_name": capability_id,
                            "description": f"Authorized operation {capability_id}",
                            "availability": {"available": True, "reason": "available"},
                            "semantic_score": 0.95 - index * 0.01,
                            "lexical_score": 2.0,
                        }
                        for index, capability_id in enumerate(candidates)
                    ]
                },
            }
        assert name == "capability.describe"
        assert arguments == {"ids": candidates}
        return {
            "ok": True,
            "status": "VERIFIED",
            "observation": {
                "capabilities": [
                    {"id": capability_id, "authorized": True}
                    for capability_id in candidates
                ]
            },
        }

    result = await attempt_capability_rescue(
        objective="Build and verify a hosted business application.",
        messages=messages,
        invoke=invoke,
    )

    assert result.applied is True
    assert list(result.candidate_ids) == candidates
    assert [name for name, _ in calls] == ["capability.search", "capability.describe"]
    assert all("I cannot do that" not in str(message.get("content") or "") for message in messages)


class _DeniedThenRecoveredAgent:
    """Exercise the real AgentRunController/AgentRuntime loop, not just the helper."""

    def __init__(self) -> None:
        self.calls = 0
        self.tool_surfaces: list[set[str]] = []

    async def chat(self, messages, tools):
        del messages
        self.calls += 1
        names = {
            str((tool.get("function") or {}).get("name") or "")
            for tool in tools
            if isinstance(tool, dict)
        }
        self.tool_surfaces.append(names)

        if self.calls == 1:
            # Simulate a model that knows the operation it needs before progressive
            # exposure has made the schema visible.
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "early-create",
                        "type": "function",
                        "function": {
                            "name": "crm.create_contact",
                            "arguments": '{"name":"Acme"}',
                        },
                    }
                ],
            }
        if self.calls == 2:
            return {
                "role": "assistant",
                "content": "I cannot create the contact because that operation is unavailable.",
            }
        if self.calls == 3:
            assert "crm.create_contact" in names
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "recovered-create",
                        "type": "function",
                        "function": {
                            "name": "crm.create_contact",
                            "arguments": '{"name":"Acme"}',
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "Created and verified the contact."}


@pytest.mark.asyncio
async def test_agent_runtime_recovers_after_unexposed_capability_denial(monkeypatch):
    async def no_resume(*, objective, metadata):
        del objective, metadata
        return None

    async def checkpoint(**kwargs):
        del kwargs
        return None

    monkeypatch.setattr(controller_module, "find_resumable_agent_run", no_resume)
    monkeypatch.setattr(controller_module, "checkpoint_agent_run", checkpoint)

    exposed = False
    invocations: list[str] = []

    search_tool = {
        "type": "function",
        "function": {
            "name": "capability.search",
            "description": "Discover an authorized operation.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            },
        },
    }
    describe_tool = {
        "type": "function",
        "function": {
            "name": "capability.describe",
            "description": "Expose exact schemas.",
            "parameters": {
                "type": "object",
                "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["ids"],
            },
        },
    }
    create_tool = {
        "type": "function",
        "function": {
            "name": "crm.create_contact",
            "description": "Create a CRM contact.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    }

    async def schemas():
        tools = [search_tool, describe_tool]
        if exposed:
            tools.append(create_tool)
        return tools

    async def invoke(name, arguments, call_id):
        nonlocal exposed
        del call_id
        invocations.append(name)
        if name == "crm.create_contact" and not exposed:
            return {
                "ok": False,
                "success": False,
                "status": "DENIED",
                "error": "Capability is not exposed in this model session",
                "retryable": True,
            }
        if name == "capability.search":
            return {
                "ok": True,
                "status": "VERIFIED",
                "observation": {
                    "capabilities": [
                        {
                            "id": "crm.create_contact",
                            "authorized": True,
                            "display_name": "Create contact",
                            "description": "Create a CRM contact.",
                            "availability": {"available": True, "reason": "available"},
                            "semantic_score": 0.98,
                            "lexical_score": 3.0,
                        }
                    ]
                },
            }
        if name == "capability.describe":
            assert arguments == {"ids": ["crm.create_contact"]}
            exposed = True
            return {
                "ok": True,
                "status": "VERIFIED",
                "observation": {
                    "capabilities": [
                        {
                            "id": "crm.create_contact",
                            "authorized": True,
                            "schema": create_tool,
                        }
                    ]
                },
            }
        assert name == "crm.create_contact"
        return {
            "ok": True,
            "status": "VERIFIED",
            "observation": {"contact_id": "contact-1", "name": arguments["name"]},
            "lifecycle": {"completed": True, "verified": True},
        }

    model = _DeniedThenRecoveredAgent()
    result = await AgentRunController(
        max_replans=0,
        verifier=_SatisfiedVerifier(),
    ).run(
        objective="Create Acme as a CRM contact.",
        model=model,
        messages=[
            {"role": "system", "content": "You are Operly."},
            {"role": "user", "content": "Create Acme as a CRM contact."},
        ],
        schemas=schemas,
        invoke=invoke,
        max_steps=6,
        inference_metadata={
            "tenant_id": "workspace-1",
            "user_id": "user-1",
            "channel": "web",
            "surface": "workspace_private",
        },
    )

    assert result["capability_rescues"] == 1
    assert result["rescued_capability_ids"] == ["crm.create_contact"]
    assert result["execution_truth"]["status"] == "VERIFIED"
    assert result["execution_truth"]["verified"] is True
    assert result["message"] == "Created and verified the contact."
    assert invocations == [
        "crm.create_contact",
        "capability.search",
        "capability.describe",
        "crm.create_contact",
    ]
    assert "crm.create_contact" not in model.tool_surfaces[0]
    assert "crm.create_contact" in model.tool_surfaces[2]
