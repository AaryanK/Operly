import asyncio
import json
from types import SimpleNamespace

import packages.software_projects.coding.source_service as source_service
from packages.software_projects.coding.opencode_agent import (
    BUILD_SYSTEM,
    CapabilityCodingAgent,
    CodingHarnessResult,
    CodingSession,
    VirtualWorkspace,
    _finish_tool,
)
from packages.software_projects.source_bundle import SourceFile


def _camera_qr_specification() -> str:
    return json.dumps(
        {
            "objective": "Employees use their cameras to scan QR codes to clock in and clock out.",
            "requirements": [
                {
                    "id": "OWNER-ROOT",
                    "requirement": "Employees use their cameras to scan QR codes to clock in and clock out.",
                    "mandatory": True,
                    "acceptance": [
                        "The executable product directly implements the requested camera and QR workflow."
                    ],
                }
            ],
            "completionPolicy": {"objectiveAuditRequired": True},
        }
    )


def _placeholder_workspace() -> VirtualWorkspace:
    workspace = VirtualWorkspace()
    workspace.write(
        "frontend/index.html",
        "<!doctype html><button id='scan'>Scan Clock In</button><div id='status'></div>",
    )
    workspace.write(
        "frontend/app.js",
        "export async function scan(){ /* Placeholder for camera logic */ return {success:true}; }",
    )
    workspace.write(
        "tests/app.test.js",
        "const test=require('node:test'); const assert=require('node:assert'); test('placeholder',()=>assert.equal(true,true));",
    )
    return workspace


def test_finish_rejects_placeholder_product_before_runtime_convergence():
    workspace = _placeholder_workspace()
    session = CodingSession(
        mode="build",
        workspace=workspace,
        before={},
        editor_context={},
        approved_specification=_camera_qr_specification(),
    )

    result = asyncio.run(_finish_tool({"summary": "done"}, session))

    assert result["ok"] is False
    assert session.finished is False
    assert "objective audit" in result["error"].lower()
    behaviors = {item["behavior"] for item in result["objectiveAudit"]["behaviorGaps"]}
    assert "camera_capture" in behaviors
    assert "qr_decode" in behaviors


def test_implicit_finish_cannot_bypass_required_objective_audit():
    workspace = _placeholder_workspace()
    session = CodingSession(
        mode="build",
        workspace=workspace,
        before={},
        editor_context={},
        approved_specification=_camera_qr_specification(),
    )

    assert CapabilityCodingAgent._can_implicit_finish(session, False) is False
    assert "Objective audit" in session.last_validation_error


def test_coding_prompt_prioritizes_product_behavior_before_scaffolding():
    lowered = BUILD_SYSTEM.lower()
    assert "work product-first" in lowered
    assert "placeholder product behavior" in lowered
    assert "deterministic objective/capability audit" in lowered


async def _model_role_scenario(monkeypatch):
    roles = []

    def fake_model_client(role):
        roles.append(role)
        return SimpleNamespace(role=role)

    result = CodingHarnessResult(
        files=[
            SourceFile("index.html", b"<!doctype html><p>ok</p>", "test"),
            SourceFile("app.test.js", b"// test", "test"),
        ],
        plan="plan",
        summary="summary",
    )

    class FakeAgent:
        def __init__(self, client=None, progress_callback=None):
            self.client = client
            self.progress_callback = progress_callback

        async def build(self, specification):
            return result

        async def edit(self, specification, files, instruction, *, context=None):
            return result

    async def fake_persist(*args, **kwargs):
        return SimpleNamespace(id="persisted"), result

    monkeypatch.setattr(source_service, "coding_model_client", fake_model_client)
    monkeypatch.setattr(source_service, "OpenCodeStyleCodingAgent", FakeAgent)
    monkeypatch.setattr(source_service, "_persist_with_contract_repair", fake_persist)

    plan = {
        "summary": "Employees use their cameras to scan QR codes.",
        "requirementLedger": [
            {
                "id": "OWNER-ROOT",
                "normalizedMeaning": "Employees use their cameras to scan QR codes.",
                "mandatory": True,
            }
        ],
    }
    plan_row = SimpleNamespace(id="plan-1", approved_version=1)

    await source_service.generate_source_for_plan(
        None,
        "tenant",
        "user",
        plan_row,
        plan,
    )
    assert roles == ["coding"]

    roles.clear()
    source = SimpleNamespace(files_json="[]")
    await source_service.edit_source_for_plan(
        None,
        "tenant",
        "user",
        plan_row,
        plan,
        source,
        "Rewrite the architecture to satisfy the owner objective.",
        edit_kind="objective_repair",
    )
    assert roles == ["repair"]

    compact = json.loads(source_service._plan_specification(plan))
    assert compact["completionPolicy"]["objectiveAuditRequired"] is True
    assert compact["implementationOrder"][0].startswith(
        "Implement the owner's literal end-to-end product behavior first"
    )


def test_greenfield_role_unchanged_but_objective_rewrite_uses_repair_pool(monkeypatch):
    asyncio.run(_model_role_scenario(monkeypatch))
