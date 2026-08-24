import pytest

from packages.agents.runtime import AgentRuntime


class ApprovalThenClaimModel:
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
                        "id": "send-1",
                        "function": {
                            "name": "gmail.send_email",
                            "arguments": '{"to":["a@example.com"],"subject":"Hi"}',
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "Sent successfully."}


@pytest.mark.asyncio
async def test_waiting_approval_overrides_false_model_completion_claim():
    async def schemas():
        return [
            {
                "type": "function",
                "function": {
                    "name": "gmail.send_email",
                    "parameters": {"type": "object"},
                },
            }
        ]

    async def invoke(name, arguments, call_id):
        return {
            "ok": True,
            "plugin": name,
            "status": "WAITING_APPROVAL",
            "action_id": "action-1",
            "approval_id": "approval-1",
            "lifecycle": {
                "terminal": False,
                "completed": False,
                "awaiting_approval": True,
                "running": False,
                "verified": False,
            },
        }

    result = await AgentRuntime(max_steps=2).run(
        model=ApprovalThenClaimModel(),
        messages=[{"role": "user", "content": "send the email"}],
        schemas=schemas,
        invoke=invoke,
    )

    assert result["message"] == (
        "Approval is required before gmail.send_email can run. "
        "The approval-gated operation has not been completed yet."
    )
    assert result["execution_truth"] == {
        "status": "WAITING_APPROVAL",
        "completed": False,
        "verified": False,
        "capability_id": "gmail.send_email",
        "action_id": "action-1",
        "approval_id": "approval-1",
    }


class FailedThenRecoveredModel:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "first",
                        "function": {"name": "demo.write", "arguments": "{}"},
                    }
                ],
            }
        if self.calls == 2:
            return {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "retry",
                        "function": {"name": "demo.write", "arguments": "{}"},
                    }
                ],
            }
        return {"role": "assistant", "content": "Completed after retry."}


@pytest.mark.asyncio
async def test_later_verified_retry_supersedes_earlier_failure():
    statuses = iter(("FAILED", "VERIFIED"))

    async def schemas():
        return [
            {
                "type": "function",
                "function": {"name": "demo.write", "parameters": {"type": "object"}},
            }
        ]

    async def invoke(name, arguments, call_id):
        status = next(statuses)
        return {
            "ok": status == "VERIFIED",
            "status": status,
            "action_id": call_id,
            "lifecycle": {
                "completed": status == "VERIFIED",
                "verified": status == "VERIFIED",
            },
        }

    result = await AgentRuntime(max_steps=3).run(
        model=FailedThenRecoveredModel(),
        messages=[{"role": "user", "content": "do it"}],
        schemas=schemas,
        invoke=invoke,
    )

    assert result["message"] == "Completed after retry."
    assert result["execution_truth"]["status"] == "VERIFIED"
    assert result["execution_truth"]["verified"] is True
