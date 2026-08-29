from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_canonical_artifact_router_is_mounted():
    source = _source("apps/api/main.py")
    assert "from apps.api.artifact_router import router as artifact_router" in source
    assert "artifact_router," in source


def test_legacy_coding_harness_router_is_deleted():
    assert not (ROOT / "apps/api/coding_harness_router.py").exists()
    source = _source("apps/api/main.py")
    assert "coding_harness_router" not in source
