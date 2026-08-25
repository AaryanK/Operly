import asyncio
from types import SimpleNamespace

import packages.agents.controller as agent_controller
import packages.agents.runtime as agent_runtime
import packages.coding_harness.studio_controller as studio


class FakeDB:
    async def commit(self):
        return None

    async def refresh(self, _row):
        return None


async def _noop(*_args, **_kwargs):
    return None


async def _controller_scenario(monkeypatch):
    source1 = SimpleNamespace(
        id="source-1",
        source_version=1,
        bundle_digest="sha256:" + "1" * 64,
        provenance_json="{}",
    )
    source2 = SimpleNamespace(
        id="source-2",
        source_version=2,
        bundle_digest="sha256:" + "2" * 64,
        provenance_json="{}",
    )
    state = {"source": source1}
    build = SimpleNamespace(id="build-2", state="preview_ready")
    submitted = []

    async def fake_latest(*_args, **_kwargs):
        return state["source"]

    def fake_audit(_plan, source):
        if source.source_version == 1:
            return {
                "verified": False,
                "classification": "objective_incomplete",
                "message": "camera/QR behavior missing",
                "unmetRequirements": [{"id": "ROOT_OBJECTIVE"}],
                "capabilityUsageGaps": [],
                "runtimeContractGaps": [],
            }
        return {
            "verified": True,
            "classification": "objective_verified",
            "message": "Original objective remains materially represented.",
            "unmetRequirements": [],
            "capabilityUsageGaps": [],
            "runtimeContractGaps": [],
        }

    async def fake_repair(*args, **_kwargs):
        evidence = args[6]
        assert evidence["classification"] == "objective_incomplete"
        assert evidence["runtimeRunId"] == "solution:qr:attempt:4"
        state["source"] = source2
        return source2, SimpleNamespace(
            changed_paths=["frontend/scanner.js", "backend/app.py"],
            summary="Restored camera QR workflow and runner port contract",
        )

    async def fake_submit(*args, **_kwargs):
        source = args[5]
        key = args[6]
        assert source is source2
        assert ":source:2:" in key
        submitted.append(key)
        return build

    async def fake_await(_db, value, _adapter, _progress):
        return value

    async def no_existing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(studio, "latest_source", fake_latest)
    monkeypatch.setattr(studio, "audit_generated_source", fake_audit)
    monkeypatch.setattr(studio, "repair_source_for_plan", fake_repair)
    monkeypatch.setattr(studio, "submit_source_build", fake_submit)
    monkeypatch.setattr(agent_controller, "checkpoint_agent_run", _noop)
    monkeypatch.setattr(agent_controller, "load_agent_run", no_existing)
    monkeypatch.setattr(agent_controller, "find_resumable_agent_run", no_existing)
    monkeypatch.setattr(agent_runtime, "ensure_model_trace_sink", lambda: None)
    monkeypatch.setattr(agent_runtime, "emit_runtime_trace_event", _noop)

    plan = {
        "summary": "Employees use their camera to scan QR codes to clock in and out.",
        "provenance": {"originalPrompt": "Employees use their camera to scan QR codes to clock in and out."},
        "requirementLedger": [
            {
                "id": "R-001",
                "mandatory": True,
                "normalizedMeaning": "Use camera QR scanning for employee clock in and clock out.",
            }
        ],
    }
    final_build, final_source, repairs = await studio.run_studio_generation(
        FakeDB(),
        "tenant",
        "user",
        SimpleNamespace(id="plan-1", approved_version=1),
        plan,
        "solution:qr:generated-build:4",
        max_repairs=2,
        metadata={
            "runtime_run_id": "solution:qr:attempt:4",
            "conversation_id": "solution:qr",
            "tenant_id": "tenant",
            "user_id": "user",
            "surface": "solution_generation",
            "channel": "solution",
        },
        await_runner_build=fake_await,
        failure_evidence=lambda _build: {},
    )

    assert final_build is build
    assert final_source is source2
    assert len(repairs) == 1
    assert repairs[0]["classification"] == "objective_incomplete"
    assert submitted and ":source:2:" in submitted[0]


def test_shared_agent_run_controller_repairs_objective_before_runner(monkeypatch):
    asyncio.run(_controller_scenario(monkeypatch))
