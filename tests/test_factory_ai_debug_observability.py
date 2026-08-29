import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apps.api.runtime_trace_router import _runtime_run_summary
from packages.agents.control_plane.runtime_aware_factory import (
    RuntimeAwareAgentFactoryControlPlane,
    _fallback_capability_intents,
)
from packages.agents.control_plane.safe_factory import SafeAgentFactoryControlPlane
from packages.model_runtime.trace_context import current_trace_metadata


def _row(
    phase: str,
    *,
    payload=None,
    second=0,
    runtime_event=True,
    provider="operly",
    model="runtime",
    component="factory",
):
    visible_payload = payload or {}
    if runtime_event:
        # Match the real persistence shape from emit_runtime_trace_event:
        # trace envelope -> event packet -> application payload.
        visible_payload = {
            "eventType": phase,
            "metadata": {"runtime_component": component},
            "payload": visible_payload,
        }
    return SimpleNamespace(
        phase=phase,
        created_at=datetime(2026, 8, 29, 5, 20, 0) + timedelta(seconds=second),
        provider=provider,
        provider_model_id=model,
        component=component,
        payload_json=json.dumps({"payload": visible_payload}),
        conversation_id="conversation-1",
        tenant_id="tenant-1",
        user_id="user-1",
        surface="workspace_shared",
        channel="web",
    )


def test_factory_trace_metadata_promotes_internal_conversation_id():
    metadata = RuntimeAwareAgentFactoryControlPlane._trace_metadata(
        {
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "surface": "workspace_shared",
            "_conversation_id": "conversation-1",
        },
        "run-1",
    )

    assert metadata["runtime_run_id"] == "run-1"
    assert metadata["runtime_controller"] == "factory"
    assert metadata["conversation_id"] == "conversation-1"


def test_ai_debug_marks_real_factory_capability_block_as_blocked():
    summary = _runtime_run_summary(
        "run-blocked",
        [
            _row("route.selected", payload={"state": "started"}),
            _row(
                "capability.rejected",
                payload={
                    "controller": "factory",
                    "state": "blocked",
                    "failure_class": "capability_missing",
                    "missing_capability_intents": ["Read calendar events"],
                    "token_usage": 1741,
                },
                second=1,
            ),
        ],
    )

    assert summary["status"] == "blocked"
    assert summary["entryCount"] == 2
    assert summary["tokenUsage"]["totalTokens"] == 1741
    assert summary["modelCandidatesObserved"] == []


def test_nonterminal_capability_rejection_does_not_mark_run_blocked():
    summary = _runtime_run_summary(
        "run-recovered",
        [
            _row("route.selected", payload={"state": "started"}),
            _row("capability.rejected", payload={"state": "rejected"}, second=1),
            _row("workflow.completed", payload={"state": "completed"}, second=2),
        ],
    )

    assert summary["status"] == "success"


def test_zero_model_completed_factory_run_is_success():
    summary = _runtime_run_summary(
        "run-complete",
        [
            _row("route.selected", payload={"state": "started"}),
            _row("workflow.completed", payload={"state": "completed"}, second=1),
        ],
    )

    assert summary["status"] == "success"


def test_correlated_model_rows_supply_real_model_and_exact_usage():
    summary = _runtime_run_summary(
        "run-model",
        [
            _row("route.selected", payload={"state": "started"}),
            _row(
                "success",
                payload={
                    "output": {
                        "usage": {
                            "input_tokens": 1400,
                            "output_tokens": 341,
                            "total_tokens": 1741,
                        }
                    }
                },
                second=1,
                runtime_event=False,
                provider="groq",
                model="compiler-model",
                component="factory_blueprint_compiler",
            ),
            _row("workflow.completed", payload={"state": "completed"}, second=2),
        ],
    )

    assert summary["status"] == "success"
    assert summary["modelCandidatesObserved"] == [
        {"provider": "groq", "model": "compiler-model"}
    ]
    assert summary["tokenUsage"] == {
        "inputTokens": 1400,
        "outputTokens": 341,
        "totalTokens": 1741,
    }


def test_fallback_multi_tool_business_request_splits_capability_families():
    prompt = (
        "Look at my calendar for tomorrow and help me get ready for the day. "
        "For each meeting, check who I’m meeting with and look through my recent emails "
        "for anything relevant to that meeting. Give me a quick summary of what I should "
        "know and what I should prepare beforehand. If there’s something I clearly need "
        "to do before a meeting, create a task for me so I don’t forget."
    )

    intents = _fallback_capability_intents(prompt)

    assert "List calendar events" in intents
    assert "Search Gmail messages" in intents
    assert "Create tasks" in intents
    assert prompt[:500] not in intents
    assert len(intents) <= 8


@pytest.mark.asyncio
async def test_factory_run_binds_nested_model_trace_scope_to_factory_run_id():
    seen_metadata = {}
    response = SimpleNamespace(
        execution=SimpleNamespace(
            blocked=False,
            completed=False,
            attempts=[],
            stop_reason="stopped",
            token_usage=0,
            external_actions=0,
        )
    )

    async def schemas():
        return []

    async def invoke(name, arguments, call_id):
        return {}

    async def fake_safe_run(self, **kwargs):
        seen_metadata.update(current_trace_metadata())
        return response

    factory = RuntimeAwareAgentFactoryControlPlane(schemas=schemas, invoke=invoke)
    with patch.object(SafeAgentFactoryControlPlane, "run", fake_safe_run), patch(
        "packages.agents.control_plane.runtime_aware_factory.emit_runtime_trace_event",
        new=AsyncMock(),
    ):
        await factory.run(
            objective="Check my calendar",
            metadata={
                "tenant_id": "tenant-1",
                "user_id": "user-1",
                "surface": "workspace_shared",
                "_conversation_id": "conversation-1",
                "runtime_run_id": "factory-run-1",
            },
            facts={"temporal_context": {}},
        )

    assert seen_metadata["runtime_run_id"] == "factory-run-1"
    assert seen_metadata["conversation_id"] == "conversation-1"
    assert seen_metadata["runtime_controller"] == "factory"
