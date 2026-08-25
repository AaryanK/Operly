from __future__ import annotations

import io
import json
from types import SimpleNamespace
from zipfile import ZipFile

from packages.capabilities.artifact_provider import ArtifactProvider
from packages.capabilities.search_index import CapabilitySearchIndex
from packages.capabilities.software_build_provider import (
    SoftwareBuildProvider,
    _generated_runner_verified,
)
from packages.custom_software.source_bundles import SourceBundle, SourceFile
from packages.software_projects import delivery as delivery_module


class _ZeroSemanticIndex:
    backend_name = "test-zero-semantic"
    degraded_reason = "forced lexical-only regression test"

    def rank(self, documents, query, *, limit):
        del documents, query, limit
        return []


def test_qr_codebase_objective_discovers_project_build_before_loose_text_file():
    software = SoftwareBuildProvider()
    artifacts = ArtifactProvider()
    definitions = [*software.capabilities, *artifacts.capabilities]
    query = (
        "Write an entire codebase for a working QR based clockin clock out application "
        "and drop me the files in here. Do complete planning and give me an entire codebase."
    )

    hits = CapabilitySearchIndex(semantic_index=_ZeroSemanticIndex()).search(
        definitions,
        query,
        limit=8,
    )

    assert hits
    assert hits[0].capability_id == "software.build"
    assert hits[0].lexical_score >= 0.75
    assert any(item.capability_id == "artifact.create_text" for item in hits)


def test_software_build_surface_does_not_expose_coding_subtools():
    ids = {definition.id for definition in SoftwareBuildProvider.capabilities}
    assert ids == {"software.build", "software.build.status", "software.source.export"}
    assert not ids & {"filesystem", "terminal", "browser", "studio.advance"}


def test_runner_verification_requires_durable_source_and_build_evidence():
    queued = SimpleNamespace(status="queued", evidence_json=json.dumps({"buildState": "queued"}))
    assert _generated_runner_verified(queued) is False

    incomplete = SimpleNamespace(
        status="succeeded",
        evidence_json=json.dumps({"buildState": "preview_ready", "buildId": "build-1"}),
    )
    assert _generated_runner_verified(incomplete) is False

    verified = SimpleNamespace(
        status="succeeded",
        evidence_json=json.dumps(
            {
                "buildState": "preview_ready",
                "buildId": "build-1",
                "sourceBundleId": "source-1",
                "sourceVersion": 3,
            }
        ),
    )
    assert _generated_runner_verified(verified) is True


def test_source_zip_is_projection_of_verified_bundle(monkeypatch):
    bundle = SourceBundle(
        files=(
            SourceFile("app.py", b"print('ok')\n", "coding_agent"),
            SourceFile("tests/test_app.py", b"def test_ok():\n    assert True\n", "coding_agent"),
        ),
        manifest={"files": []},
        digest="sha256:bundle",
    )
    monkeypatch.setattr(delivery_module, "source_bundle_from_record", lambda source: bundle)
    source = SimpleNamespace(source_version=2)

    first = delivery_module.generated_source_archive_bytes(source)
    second = delivery_module.generated_source_archive_bytes(source)

    assert first == second
    with ZipFile(io.BytesIO(first), "r") as archive:
        assert archive.namelist() == ["app.py", "tests/test_app.py"]
        assert archive.read("app.py") == b"print('ok')\n"
