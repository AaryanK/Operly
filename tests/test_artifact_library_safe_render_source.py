"""Regression guard: artifact library must not HTML-parse server-supplied metadata.

This source-level check is intentionally dependency-free. Browser-level validation
is still required before merge.
"""
from pathlib import Path


def test_artifact_library_uses_text_nodes_for_untrusted_metadata():
    router = (Path(__file__).resolve().parents[1] / "apps/api/artifact_router.py").read_text()
    ui = router.split('async def artifact_ingress_ui(', 1)[1]
    assert "recent.replaceChildren()" in ui
    assert "link.textContent=a.filename" in ui
    assert "code.textContent=a.artifact_id" in ui
    assert "encodeURIComponent(a.artifact_id)" in ui
    assert "innerHTML" not in ui
