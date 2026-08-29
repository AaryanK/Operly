import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from apps.api.runtime_trace_router import _runtime_run_summary
from packages.agents.control_plane.runtime_aware_factory import RuntimeAwareAgentFactoryControlPlane


def _row(phase: str, *, payload=None, second=0):
    return SimpleNamespace(
        phase=phase,
        created_at=datetime(2026, 8, 29, 5, 20, 0) + timedelta(seconds=second),
        provider="operly",
        provider_model_id="runtime",
        component="factory",
        payload_json=json.dumps({"payload": payload or {}}),
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


def test_ai_debug_marks_zero_model_factory_capability_block_as_blocked():
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
                },
                second=1,
            ),
        ],
    )

    assert summary["status"] == "blocked"
    assert summary["entryCount"] == 2
    assert summary["tokenUsage"]["totalTokens"] == 0


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
