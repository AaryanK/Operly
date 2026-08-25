from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from apps.api.custom_software_router import _build_result, create_runner_build, repair_runner_build
from packages.software_projects.planning.schema import RunnerBuildInput, RunnerRepairInput


def _auth(role="owner"):
    return SimpleNamespace(
        role=role,
        tenant=SimpleNamespace(id="tenant-1"),
        user=SimpleNamespace(id="user-1"),
    )


class FakeDB:
    async def scalar(self, _statement):
        return None


@pytest.mark.asyncio
async def test_custom_software_build_uses_generic_coding_harness_source_loop():
    payload=RunnerBuildInput(planId="plan-1",approvedVersion=2,idempotencyKey="build-key-123")
    row=SimpleNamespace(id="plan-1",status="approved",approved_version=2)
    plan=SimpleNamespace()
    expected={"state":"preview_ready","runtime":"python-stdlib-web"}

    with patch("apps.api.custom_software_router.owned_plan",new=AsyncMock(return_value=row)), \
         patch("apps.api.custom_software_router.plan_version",new=AsyncMock(return_value=(SimpleNamespace(),plan))), \
         patch("apps.api.custom_software_router._build_result",new=AsyncMock(return_value=expected)) as build_result:
        result=await create_runner_build(payload,auth=_auth(),db=FakeDB())

    assert result==expected
    build_result.assert_awaited_once()
    args=build_result.await_args.args
    assert args[1].tenant.id=="tenant-1"
    assert args[2] is row
    assert args[3] is plan
    assert args[4] is payload


@pytest.mark.asyncio
async def test_custom_software_build_rejects_unapproved_plan_before_model_or_runner_work():
    payload=RunnerBuildInput(planId="plan-1",approvedVersion=2,idempotencyKey="build-key-123")
    row=SimpleNamespace(id="plan-1",status="draft",approved_version=None)

    with patch("apps.api.custom_software_router.owned_plan",new=AsyncMock(return_value=row)), \
         patch("apps.api.custom_software_router.plan_version",new=AsyncMock(return_value=(SimpleNamespace(),SimpleNamespace()))), \
         patch("apps.api.custom_software_router._build_result",new=AsyncMock()) as build_result:
        with pytest.raises(HTTPException) as raised:
            await create_runner_build(payload,auth=_auth(),db=FakeDB())

    assert raised.value.status_code==409
    build_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_result_returns_source_provenance_and_bounded_repairs():
    build=SimpleNamespace(id="build-1",state="tests_failed")
    source=SimpleNamespace(id="source-2")
    repairs=[{"repairNumber":1,"fromSourceVersion":1,"toSourceVersion":2}]
    payload=RunnerBuildInput(planId="plan-1",approvedVersion=2,idempotencyKey="build-key-123")

    with patch("apps.api.custom_software_router.build_with_repair",new=AsyncMock(return_value=(build,source,repairs))) as loop, \
         patch("apps.api.custom_software_router.build_json",return_value={"id":"build-1","state":"tests_failed"}), \
         patch("apps.api.custom_software_router.source_record_json",return_value={"id":"source-2","runtimeProfile":"python-stdlib-web"}):
        result=await _build_result(FakeDB(),_auth(),SimpleNamespace(id="plan-1"),SimpleNamespace(),payload)

    loop.assert_awaited_once()
    assert result["source"]["runtimeProfile"]=="python-stdlib-web"
    assert result["repairCount"]==1
    assert result["repairAttempts"]==repairs


@pytest.mark.asyncio
async def test_manual_repair_reenters_same_generic_build_repair_loop():
    failed=SimpleNamespace(id="failed-build",plan_id="plan-1")
    row=SimpleNamespace(id="plan-1",status="approved",approved_version=3)
    plan=SimpleNamespace()
    repair_payload=RunnerRepairInput(idempotencyKey="repair-key-123")
    expected={"state":"preview_ready","repairCount":1}

    with patch("apps.api.custom_software_router.owned_build",new=AsyncMock(return_value=failed)), \
         patch("apps.api.custom_software_router.owned_plan",new=AsyncMock(return_value=row)), \
         patch("apps.api.custom_software_router.plan_version",new=AsyncMock(return_value=(SimpleNamespace(),plan))), \
         patch("apps.api.custom_software_router._build_result",new=AsyncMock(return_value=dict(expected))) as build_result:
        result=await repair_runner_build("failed-build",repair_payload,auth=_auth(),db=FakeDB())

    assert result["requestedFromBuildId"]=="failed-build"
    assert result["state"]=="preview_ready"
    request=build_result.await_args.args[4]
    assert request.planId=="plan-1"
    assert request.approvedVersion==3
    assert request.idempotencyKey=="repair-key-123"
