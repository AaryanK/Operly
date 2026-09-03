from fastapi import HTTPException
import pytest

from packages.plugins.hosted_public_router import _asset_path


def test_hosted_plugin_root_defaults_to_index():
    assert _asset_path("") == "index.html"
    assert _asset_path(None) == "index.html"


def test_hosted_plugin_asset_path_normalizes_leading_slash():
    assert _asset_path("/assets/app.js") == "assets/app.js"


@pytest.mark.parametrize("value", ["../secret", "assets/../../secret", "./index.html"])
def test_hosted_plugin_asset_path_rejects_traversal(value: str):
    with pytest.raises(HTTPException) as error:
        _asset_path(value)
    assert error.value.status_code == 404
