"""Safe delivery projections for canonical software source.

Source remains authoritative in backend storage. Archives are scoped delivery
artifacts only and never become an execution input or editable source of truth.
"""
from __future__ import annotations

import io
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from packages.artifacts.service import ArtifactScope, ArtifactService, artifact_json
from packages.software_projects.coding.build_service import source_bundle_from_record
from packages.software_projects.source_service import files_from_row


def _archive_name(value:str|None,*,source_version:int)->str:
    raw=Path(str(value or f"source-v{source_version}.zip")).name.replace("\x00","").strip()
    if not raw.lower().endswith(".zip"):raw=f"{raw}.zip"
    return raw[:255] or f"source-v{source_version}.zip"


def _zip_bytes(files:dict[str,str])->bytes:
    output=io.BytesIO()
    with ZipFile(output,"w",compression=ZIP_DEFLATED,compresslevel=9) as archive:
        for path,content in sorted(files.items()):
            info=ZipInfo(path,date_time=(1980,1,1,0,0,0));info.compress_type=ZIP_DEFLATED;info.external_attr=0o100644<<16;archive.writestr(info,content)
    return output.getvalue()


def generated_source_archive_bytes(source)->bytes:
    bundle=source_bundle_from_record(source);return _zip_bytes({item.path:item.content for item in bundle.files})

def canonical_source_archive_bytes(source)->bytes:return _zip_bytes(files_from_row(source))


async def persist_generated_source_archive(db,*,tenant_id:str,created_by:str,source,filename:str|None=None,run_id:str|None=None)->dict:
    raw=generated_source_archive_bytes(source);row=await ArtifactService(db).create_bytes(ArtifactScope("workspace",tenant_id,tenant_id=tenant_id),filename=_archive_name(filename,source_version=int(source.source_version)),content_type="application/zip",content=raw,source="software_source_export",created_by=created_by,run_id=run_id,metadata={"artifact_kind":"software_source_archive","source_bundle_id":source.id,"source_version":source.source_version,"bundle_digest":source.bundle_digest,"authoritative":False,"projection_only":True,"executed":False});return artifact_json(row)


async def persist_canonical_source_archive(db,*,tenant_id:str,created_by:str,source,filename:str|None=None,run_id:str|None=None)->dict:
    raw=canonical_source_archive_bytes(source);row=await ArtifactService(db).create_bytes(ArtifactScope("workspace",tenant_id,tenant_id=tenant_id),filename=_archive_name(filename,source_version=int(source.source_version)),content_type="application/zip",content=raw,source="software_source_export",created_by=created_by,run_id=run_id,metadata={"artifact_kind":"software_source_archive","software_project_id":source.project_id,"canonical_source_version_id":source.id,"source_version":source.source_version,"bundle_digest":source.bundle_digest,"authoritative":False,"projection_only":True,"executed":False});return artifact_json(row)


__all__=["generated_source_archive_bytes","canonical_source_archive_bytes","persist_generated_source_archive","persist_canonical_source_archive"]
