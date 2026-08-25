from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_personal_approval_api_is_owner_scoped_and_uses_personal_action_methods():
    source = (ROOT / "apps/api/approvals_router.py").read_text(encoding="utf-8")
    assert '@router.get("/personal")' in source
    assert '@router.patch("/personal/{approval_id}")' in source
    assert "Approval.owner_user_id == auth.user.id" in source
    assert "service.approve_personal(auth.user.id, business_action_id)" in source
    assert "service.reject_personal(auth.user.id, business_action_id)" in source


def test_personal_approval_rebuilds_account_owned_provider_authority():
    source = (ROOT / "apps/api/approvals_router.py").read_text(encoding="utf-8")
    assert "PersonalGoogleCapabilityProvider" in source
    assert "google.registry_for(db, user_id=user_id)" in source
    assert "authority=set(PERSONAL_EXECUTION_PERMISSIONS)" in source
    assert "PERSONAL_APPROVAL_PROVIDER_UNAVAILABLE" in source


def test_workspace_approval_api_remains_workspace_scoped():
    source = (ROOT / "apps/api/approvals_router.py").read_text(encoding="utf-8")
    assert 'Approval.scope_kind == "workspace"' in source
    assert "Approval.tenant_id == auth.tenant.id" in source
    assert "Approval.owner_user_id.is_(None)" in source
