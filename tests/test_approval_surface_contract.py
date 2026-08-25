from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_personal_surface_exposes_approval_controls_and_payload():
    source = (ROOT / "apps/web/src/account/PersonalHome.tsx").read_text(encoding="utf-8")
    assert 'api<Approval[]>("/approvals/personal")' in source
    assert "/approvals/personal/${encodeURIComponent(id)}" in source
    assert "Full action payload" in source
    assert "Approve" in source and "Reject" in source


def test_workspace_activity_uses_full_approval_surface():
    shell = (ROOT / "apps/web/src/workspace/WorkspaceShell.tsx").read_text(encoding="utf-8")
    activity = (ROOT / "apps/web/src/workspace/ActivityPage.tsx").read_text(encoding="utf-8")
    assert 'import("./ActivityPage")' in shell
    assert "Full action payload" in activity
    assert 'api<Row[]>("/approvals")' in activity
