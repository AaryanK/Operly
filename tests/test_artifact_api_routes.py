from apps.api.main import app


def test_artifact_routes_exist_only_under_api_prefix():
    paths = set(app.openapi()["paths"])
    assert "/api/artifacts" in paths
    assert "/api/artifacts/upload" in paths
    assert "/api/artifacts/{artifact_id}/download" in paths
    assert "/api/personal/artifacts" in paths
    assert "/api/personal/artifacts/upload" in paths
    assert "/api/personal/artifacts/{artifact_id}/download" in paths
    assert "/artifacts" not in paths
    assert "/artifacts/upload" not in paths
