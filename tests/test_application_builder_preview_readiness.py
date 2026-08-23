import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from apps.api.application_builder_router import _assert_previewable_version


class ApplicationBuilderPreviewReadinessTests(unittest.TestCase):
    def test_blank_bootstrap_is_not_previewable(self):
        version = SimpleNamespace(version_number=1, summary="Blank application")
        with self.assertRaises(HTTPException) as raised:
            _assert_previewable_version(version)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["code"], "generation_not_ready")

    def test_generated_or_legacy_version_remains_previewable(self):
        _assert_previewable_version(SimpleNamespace(version_number=2, summary="Applied generated change"))
        _assert_previewable_version(SimpleNamespace(version_number=1, summary="Initial"))


if __name__ == "__main__":
    unittest.main()
