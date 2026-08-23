import asyncio
import json
from types import SimpleNamespace

import packages.coding_harness.execution_loop as loop
from packages.coding_harness.build_service import RunnerProfileUnsupported
from packages.runtime_plugins import FULLSTACK_RUNTIME_ID


class FakeDB:
    async def commit(self):
        return None

    async def refresh(self, _):
        return None


async def _runtime_capability_mismatch_scenario(monkeypatch):
    source1 = SimpleNamespace(id="s1", source_version=1)
    source2 = SimpleNamespace(id="s2", source_version=2)
    row = SimpleNamespace(id="p1", approved_version=1)
    final_build = SimpleNamespace(id="b2", state="preview_ready", result_json="{}", failure_classification=None, attempt=1)
    submissions = 0

    async def latest_source(*_):
        return source1

    async def submit(*args, **kwargs):
        nonlocal submissions
        submissions += 1
        if submissions == 1:
            raise RunnerProfileUnsupported("static-web-js", ["python-stdlib-web"])
        assert args[5] is source2
        return final_build

    async def repair(*args, **kwargs):
        evidence = args[6]
        assert evidence["classification"] == "runner_profile_unsupported"
        assert evidence["supportedProfiles"] == ["python-stdlib-web"]
        return source2, SimpleNamespace(changed_paths=["app.py", "build.py", "test_app.py"], summary="Adapted runtime shape")

    monkeypatch.setattr(loop, "latest_source", latest_source)
    monkeypatch.setattr(loop, "submit_source_build", submit)
    monkeypatch.setattr(loop, "repair_source_for_plan", repair)

    build, source, repairs = await loop.build_with_repair(FakeDB(), "tenant", "user", row, object(), "abcdefgh", max_repairs=2)
    assert build is final_build
    assert source is source2
    assert repairs[0]["classification"] == "runner_profile_unsupported"
    assert repairs[0]["toSourceVersion"] == 2


def test_runtime_capability_mismatch_is_repaired_into_supported_source(monkeypatch):
    asyncio.run(_runtime_capability_mismatch_scenario(monkeypatch))


async def _fullstack_capability_mismatch_scenario(monkeypatch):
    source = SimpleNamespace(id="fullstack-s1", source_version=1)
    row = SimpleNamespace(id="p1", approved_version=1)
    repair_calls = 0

    async def latest_source(*_):
        return source

    async def submit(*args, **kwargs):
        raise RunnerProfileUnsupported(
            FULLSTACK_RUNTIME_ID,
            ["python-stdlib-web", "static-web-js"],
            required_version=1,
        )

    async def repair(*args, **kwargs):
        nonlocal repair_calls
        repair_calls += 1
        raise AssertionError("Full-stack infrastructure absence must not be treated as a source repair")

    monkeypatch.setattr(loop, "latest_source", latest_source)
    monkeypatch.setattr(loop, "submit_source_build", submit)
    monkeypatch.setattr(loop, "repair_source_for_plan", repair)

    try:
        await loop.build_with_repair(
            FakeDB(),
            "tenant",
            "user",
            row,
            object(),
            "abcdefgh",
            max_repairs=2,
        )
    except RunnerProfileUnsupported as error:
        assert error.profile_id == FULLSTACK_RUNTIME_ID
    else:
        raise AssertionError("Missing full-stack runner support must remain an infrastructure failure")
    assert repair_calls == 0


def test_fullstack_capability_mismatch_never_downshifts_product_requirements(monkeypatch):
    asyncio.run(_fullstack_capability_mismatch_scenario(monkeypatch))


async def _runner_test_failure_scenario(monkeypatch):
    source1 = SimpleNamespace(id="s1", source_version=1)
    source2 = SimpleNamespace(id="s2", source_version=2)
    row = SimpleNamespace(id="p1", approved_version=1)
    failed = SimpleNamespace(id="b1", state="tests_failed", result_json=json.dumps({"failureEvidence": {"classification": "test_failure", "log": "expected 4 got 5"}}), failure_classification="test_failure", attempt=1)
    passed = SimpleNamespace(id="b2", state="preview_ready", result_json="{}", failure_classification=None, attempt=1)
    builds = [failed, passed]

    async def latest_source(*_):
        return source1

    async def submit(*args, **kwargs):
        return builds.pop(0)

    async def repair(*args, **kwargs):
        evidence = args[6]
        assert evidence["classification"] == "test_failure"
        assert "expected 4 got 5" in evidence["log"]
        return source2, SimpleNamespace(changed_paths=["js/engine.js"], summary="Fixed calculation")

    monkeypatch.setattr(loop, "latest_source", latest_source)
    monkeypatch.setattr(loop, "submit_source_build", submit)
    monkeypatch.setattr(loop, "repair_source_for_plan", repair)

    build, source, repairs = await loop.build_with_repair(FakeDB(), "tenant", "user", row, object(), "abcdefgh", max_repairs=2)
    assert build is passed
    assert source is source2
    assert repairs == [{"repairNumber": 1, "classification": "test_failure", "failedBuildId": "b1", "fromSourceVersion": 1, "toSourceVersion": 2, "changedPaths": ["js/engine.js"], "summary": "Fixed calculation"}]


def test_runner_test_failure_is_fed_back_to_same_source_workspace(monkeypatch):
    asyncio.run(_runner_test_failure_scenario(monkeypatch))
