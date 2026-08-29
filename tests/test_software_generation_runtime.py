import asyncio
from pathlib import Path
from types import SimpleNamespace

import packages.coding_harness.execution_loop as execution_loop


class FakeDB:
    async def commit(self):
        return None

    async def refresh(self, _row):
        return None


def test_legacy_studio_controller_is_removed():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "packages/coding_harness/studio_controller.py").exists()


def test_generic_coding_loop_owns_source_scoped_retry_keys(monkeypatch):
    source1 = SimpleNamespace(
        id="source-1",
        source_version=1,
        bundle_digest="sha256:" + "a" * 64,
        manifest_json="{}",
        provenance_json="{}",
        plan_id="plan-1",
        plan_version=1,
    )
    source2 = SimpleNamespace(
        id="source-2",
        source_version=2,
        bundle_digest="sha256:" + "b" * 64,
        manifest_json="{}",
        provenance_json="{}",
        plan_id="plan-1",
        plan_version=1,
    )
    failed = SimpleNamespace(
        id="build-1",
        state="tests_failed",
        failure_classification="test_failure",
        result_json='{"failureEvidence":{"classification":"test_failure","message":"expected 4 got 5"}}',
        attempt=1,
    )
    passed = SimpleNamespace(
        id="build-2",
        state="preview_ready",
        failure_classification=None,
        result_json="{}",
        attempt=2,
    )
    builds = [failed, passed]
    observed_keys = []

    async def latest(*_args, **_kwargs):
        return source1

    async def submit(*args, **_kwargs):
        observed_keys.append(args[6])
        return builds.pop(0)

    async def repair(*args, **_kwargs):
        evidence = args[6]
        assert evidence["classification"] == "test_failure"
        return source2, SimpleNamespace(changed_paths=["app.py"], summary="Fixed failing behavior")

    async def no_trace(*_args, **_kwargs):
        return None

    monkeypatch.setattr(execution_loop, "latest_source", latest)
    monkeypatch.setattr(execution_loop, "submit_source_build", submit)
    monkeypatch.setattr(execution_loop, "repair_source_for_plan", repair)
    monkeypatch.setattr(execution_loop, "_trace", no_trace)

    actual = asyncio.run(
        execution_loop.build_with_repair(
            FakeDB(),
            "tenant",
            "user",
            SimpleNamespace(id="plan-1", approved_version=1),
            object(),
            "solution:solution-123:generated-build:9",
            max_repairs=1,
        )
    )

    assert actual[0] is passed
    assert actual[1] is source2
    assert len(actual[2]) == 1
    assert observed_keys == [
        "solution:solution-123:generated-build:9",
        "solution:solution-123:generated-build:9-repair-1",
    ]
