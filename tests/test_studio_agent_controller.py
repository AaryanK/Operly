import asyncio
from types import SimpleNamespace

import packages.agents.controller as agent_controller
import packages.agents.runtime as agent_runtime
import packages.software_projects.coding.studio_controller as studio


class FakeDB:
    async def commit(self):
        return None

    async def refresh(self, _row):
        return None


async def _noop(*_args, **_kwargs):
    return None


def _patch_controller_runtime(monkeypatch):
    async def no_existing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_controller, "checkpoint_agent_run", _noop)
    monkeypatch.setattr(agent_controller, "load_agent_run", no_existing)
    monkeypatch.setattr(agent_controller, "find_resumable_agent_run", no_existing)
    monkeypatch.setattr(agent_runtime, "ensure_model_trace_sink", lambda: None)
    monkeypatch.setattr(agent_runtime, "emit_runtime_trace_event", _noop)


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
                "behaviorGaps": [],
                "capabilityUsageGaps": [],
                "authorityGaps": [],
                "runtimeContractGaps": [],
            }
        return {
            "verified": True,
            "classification": "objective_verified",
            "message": "Original objective remains materially represented.",
            "unmetRequirements": [],
            "behaviorGaps": [],
            "capabilityUsageGaps": [],
            "authorityGaps": [],
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

    monkeypatch.setattr(studio, "latest_source", fake_latest)
    monkeypatch.setattr(studio, "audit_generated_source", fake_audit)
    monkeypatch.setattr(studio, "repair_source_for_plan", fake_repair)
    monkeypatch.setattr(studio, "submit_source_build", fake_submit)
    _patch_controller_runtime(monkeypatch)

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
    assert repairs[0]["repairMode"] == "minimal_runner_repair"
    assert submitted and ":source:2:" in submitted[0]


def test_shared_agent_run_controller_repairs_objective_before_runner(monkeypatch):
    asyncio.run(_controller_scenario(monkeypatch))


def test_coding_plan_promotes_literal_owner_request_above_planner_summary():
    literal = "Employees should clock in using their cameras by scanning a QR code and clock out using another QR code."
    projected = studio._coding_plan(
        {
            "summary": "Create an attendance workflow.",
            "primaryGoal": "Track attendance.",
            "provenance": {"originalPrompt": literal},
            "requirementLedger": [
                {"id": "R-001", "mandatory": True, "normalizedMeaning": "Track attendance."}
            ],
        }
    )
    assert projected["summary"] == literal
    assert projected["primaryGoal"] == literal
    assert projected["provenance"]["originalPrompt"] == literal
    assert projected["requirementLedger"][0]["id"] == "OWNER-ROOT"
    assert projected["requirementLedger"][0]["exactText"] == literal
    assert "placeholders" in projected["requirementLedger"][0]["acceptanceCriteria"][0].lower()


async def _architectural_repair_scenario(monkeypatch):
    literal = "Employees should clock in using their cameras by scanning a QR code and clock out using another QR code."
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
    architectural_edits = []

    async def fake_latest(*_args, **_kwargs):
        return state["source"]

    def fake_audit(plan, source):
        assert plan["summary"] == literal
        assert plan["requirementLedger"][0]["id"] == "OWNER-ROOT"
        if source.source_version == 1:
            return {
                "verified": False,
                "classification": "objective_incomplete",
                "message": "camera/QR and authoritative capability behavior missing",
                "unmetRequirements": [{"id": "OWNER-ROOT"}],
                "behaviorGaps": [
                    {"behavior": "camera_capture", "reason": "missing camera"},
                    {"behavior": "qr_decode", "reason": "missing decoder"},
                ],
                "capabilityUsageGaps": [
                    {"capabilityId": "data.relational", "reason": "binding not called"}
                ],
                "authorityGaps": ["hard-coded identity"],
                "runtimeContractGaps": [],
            }
        return {
            "verified": True,
            "classification": "objective_verified",
            "message": "Original objective remains materially represented.",
            "unmetRequirements": [],
            "behaviorGaps": [],
            "capabilityUsageGaps": [],
            "authorityGaps": [],
            "runtimeContractGaps": [],
        }

    async def fake_edit(*args, **kwargs):
        coding_plan = args[4]
        instruction = args[6]
        assert coding_plan["summary"] == literal
        assert kwargs["edit_kind"] == "objective_repair"
        assert "rewrite" in instruction.lower()
        assert "placeholders" in instruction.lower()
        architectural_edits.append(instruction)
        state["source"] = source2
        return source2, SimpleNamespace(
            changed_paths=["frontend/app.js", "frontend/index.html", "backend/app.py", "tests/test_clock.js"],
            summary="Rebuilt camera QR attendance workflow end to end",
        )

    async def forbidden_minimal_repair(*_args, **_kwargs):
        raise AssertionError("Architectural objective gaps must not use minimal runner repair")

    async def fake_submit(*args, **_kwargs):
        assert args[5] is source2
        return build

    async def fake_await(_db, value, _adapter, _progress):
        return value

    monkeypatch.setattr(studio, "latest_source", fake_latest)
    monkeypatch.setattr(studio, "audit_generated_source", fake_audit)
    monkeypatch.setattr(studio, "edit_source_for_plan", fake_edit)
    monkeypatch.setattr(studio, "repair_source_for_plan", forbidden_minimal_repair)
    monkeypatch.setattr(studio, "submit_source_build", fake_submit)
    _patch_controller_runtime(monkeypatch)

    plan = {
        "summary": "Generic attendance workflow",
        "provenance": {"originalPrompt": literal},
        "requirementLedger": [
            {"id": "R-001", "mandatory": True, "normalizedMeaning": "Record attendance."}
        ],
    }
    final_build, final_source, repairs = await studio.run_studio_generation(
        FakeDB(),
        "tenant",
        "user",
        SimpleNamespace(id="plan-2", approved_version=1),
        plan,
        "solution:qr:generated-build:1",
        max_repairs=2,
        metadata={
            "runtime_run_id": "solution:qr:attempt:1",
            "generation_attempt": 1,
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
    assert architectural_edits
    assert len(repairs) == 1
    assert repairs[0]["repairMode"] == "architectural_rewrite"


def test_structural_objective_gap_allows_architectural_rewrite(monkeypatch):
    asyncio.run(_architectural_repair_scenario(monkeypatch))
