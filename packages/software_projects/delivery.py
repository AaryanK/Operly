"""Safe delivery projections for canonical software source.

The immutable source bundle remains authoritative. Archives created here are only
scoped delivery artifacts for chat/Discord/download surfaces and never become an
execution input or a second editable source of truth.
"""
from __future__ import annotations

import io
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from packages.artifacts.service import ArtifactScope, ArtifactService, artifact_json
from packages.coding_harness.build_service import source_bundle_from_record


def _archive_name(value: str | None, *, source_version: int) -> str:
    raw = Path(str(value or f"source-v{source_version}.zip")).name.replace("\x00", "").strip()
    if not raw.lower().endswith(".zip"):
        raw = f"{raw}.zip"
    return raw[:255] or f"source-v{source_version}.zip"


def generated_source_archive_bytes(source) -> bytes:
    """Build a deterministic, traversal-safe ZIP from a verified immutable bundle."""
    bundle = source_bundle_from_record(source)
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for item in bundle.files:
            # source_bundle_from_record -> build_bundle has already revalidated every
            # model-authored path and digest. Use a fixed timestamp/permissions so the
            # projection is deterministic and does not inherit host metadata.
            info = ZipInfo(item.path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, item.content)
    return output.getvalue()


async def persist_generated_source_archive(
    db,
    *,
    tenant_id: str,
    created_by: str,
    source,
    filename: str | None = None,
    run_id: str | None = None,
) -> dict:
    """Persist one verified source-bundle projection as a Workspace-scoped artifact."""
    raw = generated_source_archive_bytes(source)
    row = await ArtifactService(db).create_bytes(
        ArtifactScope("workspace", tenant_id, tenant_id=tenant_id),
        filename=_archive_name(filename, source_version=int(source.source_version)),
        content_type="application/zip",
        content=raw,
        source="software_source_export",
        created_by=created_by,
        run_id=run_id,
        metadata={
            "artifact_kind": "software_source_archive",
            "source_bundle_id": source.id,
            "source_version": source.source_version,
            "bundle_digest": source.bundle_digest,
            "authoritative": False,
            "projection_only": True,
            "executed": False,
        },
    )
    return artifact_json(row)


__all__ = ["generated_source_archive_bytes", "persist_generated_source_archive"]
